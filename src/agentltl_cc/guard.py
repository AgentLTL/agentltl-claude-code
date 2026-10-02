"""
agentltl_cc/guard.py – decide one Claude Code tool call against the rules.

    guard = Guard(ruleset, Paths(cwd, root))
    guard.restore(state)                 # trace + enforcer counters of this session
    verdict = guard.decide("Bash", {"command": "git push"}, auto=False)
    verdict.action                       # "none" | "deny" | "ask" | "stop"
    ...after the call ran...
    guard.record("Bash", {"command": "pytest"}, "toolu_1", "3 passed")
    state = guard.dump()

``Bash`` calls are translated by cli-to-tools into the structured calls of the command line
(``git commit -m x && git push`` → ``git_commit``, ``git_push``) and checked all or nothing.
Every other tool is checked under its Claude Code name with its input as arguments.

The guard never approves anything: "none" means it has no objection and Claude Code's own
permission flow (your settings, auto mode) still decides.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from cli_to_tools import SpecRegistry, ToolCall, TranslationError, Translator
from cli_to_tools.agentltl import CliConstraintEnforcer

from .match import Paths
from .rules import MODES, Rule, RuleSet

SHELL_TOOLS = {"Bash": "command"}
AUTO_MODES = ("auto", "bypassPermissions", "dontAsk")
# enforcer state carried between hook processes, besides the trace itself
_ENGINE_FIELDS = (
    "_soft_block_counts", "_consecutive_soft_block_counts", "_block_and_warn_counts",
    "_persistent_block_counts", "_last_blocked_call", "_current_generation",
    "_constraint_violations", "_soft_blocked_calls", "_block_and_warn_overrides",
    "_blocked_command",
)
_MAX_TRACE = 5000
_MAX_LOG = 200
_MAX_STRING = 2000

_TAILS = {
    "block": "This rule cannot be overridden by you. Do something that satisfies it instead, "
             "or explain the situation to the user.",
    "warn": "This is a warning. If you are sure the call is right, you may override the rule by "
            "repeating exactly the same call as your next action; otherwise comply.",
    "retry": "Change your approach to satisfy the rule. If you keep getting blocked, the user "
             "will be asked to decide.",
    "ask": "The user has been asked whether to allow this call.",
    "stop": "This rule stops the session. Stop working and tell the user what you were trying "
            "to do and why.",
}


@dataclass
class Verdict:
    """What the hook should tell Claude Code.

    action: "none" (no objection), "deny", "ask" (the user decides) or "stop" (deny and halt).
    reason: Shown with deny/ask/stop. context: extra text for Claude, whatever the action.
    """

    action: str = "none"
    reason: str = ""
    context: str = ""
    rule: Optional[str] = None
    calls: List[Dict[str, Any]] = field(default_factory=list)


class GuardTranslator(Translator):
    """cli-to-tools translator that also exposes output redirections as ``redirect_to``.

    ``echo x > .env`` then matches ``where: {redirect_to: "**/.env"}`` like a Write would.
    """

    def _call(self, node: Any, command: str, call_id: str, index: int) -> ToolCall:
        call = super()._call(node, command, call_id, index)
        targets = [r["target"] for r in node.redirects
                   if r.get("target") and ">" in r.get("op", "") and r.get("fd") not in ("2",)
                   and not r["target"].startswith("&")]
        if targets:
            call.args["redirect_to"] = targets
        return call


def translator_for(ruleset: RuleSet) -> GuardTranslator:
    registry = SpecRegistry()
    if ruleset.tool_specs:
        registry.load_dict(ruleset.tool_specs)
    return GuardTranslator(registry)


# runners whose subcommands cli-to-tools does not split into tools (`make test` -> make, argv)
_RUNNERS = ("make", "npm", "yarn", "pnpm", "npx", "cargo", "go", "uv", "poetry", "just", "bun")
_ANY_TOOL_ARGS = ("*", "redirect_to", "extra_args")


def lint(ruleset: RuleSet, registry: Optional[SpecRegistry] = None) -> List[str]:
    """Warnings for targets that can never match: unknown tools or argument names.

    The rule file is valid without them; these catch the rule that silently never fires
    (``with: {repository: origin}`` on ``git_push``, whose argument is ``remote``) or, for
    ``require``, fires on every call.
    """
    registry = registry or translator_for(ruleset).registry
    schemas = {s["name"]: set(s.get("parameters", {}).get("properties", {}))
               for s in registry.tool_schemas()}
    out: List[str] = []
    for rule in ruleset.rules:
        for target in _flatten(rule.targets):
            keys = set(target.with_) | set(target.where)
            for tool in target.tools:
                if tool[:1].isupper() or tool.startswith("mcp__"):
                    continue
                if tool in schemas:
                    unknown = sorted(k for k in keys - set(_ANY_TOOL_ARGS) if k not in schemas[tool])
                    if unknown:
                        out.append(f"{rule.id}: {tool} has no argument(s) {unknown}; it has "
                                   f"{sorted(schemas[tool])}")
                    continue
                prefix = tool.split("_", 1)[0]
                if "_" in tool and registry.get(prefix) is not None:
                    # a subcommand the spec does not declare (`git notes` -> git_notes) keeps
                    # its words in `argv`
                    unknown = sorted(keys - set(_ANY_TOOL_ARGS) - {"argv"})
                    if unknown:
                        out.append(f"{rule.id}: {tool} is not a declared subcommand, so its "
                                   f"only argument is 'argv'; {unknown} never match.")
                elif "_" in tool and prefix in _RUNNERS:
                    out.append(f"{rule.id}: no command translates to '{tool}'. Check with "
                               f"`agentltl translate \"{tool.replace('_', ' ', 1)}\"`; "
                               f"a command without a spec is '{prefix}' with an 'argv' list, "
                               f"e.g. {{tool: {prefix}, with: {{argv: ...}}}}")
                elif registry.get(tool) is None:
                    unknown = sorted(keys - set(_ANY_TOOL_ARGS) - {"argv"})
                    if unknown:
                        out.append(f"{rule.id}: '{tool}' has no spec, so its only argument is "
                                   f"'argv'; {unknown} never match. Add a spec under 'tools:'.")
    return out


def _flatten(targets: Any) -> List[Any]:
    out: List[Any] = []
    for t in targets:
        out += _flatten(t.targets) if hasattr(t, "targets") else [t]
    return out


def is_auto(permission_mode: Optional[str]) -> bool:
    return permission_mode in AUTO_MODES


class Guard:
    def __init__(self, ruleset: RuleSet, paths: Optional[Paths] = None) -> None:
        from agentltl import ConstraintSeverity

        self.ruleset = ruleset
        self.paths = paths or Paths(os.getcwd())
        self.translator = translator_for(ruleset)
        self.enforcer = CliConstraintEnforcer(
            constraints=ruleset.constraints(),
            constraint_severities=ruleset.severities(),
            default_severity=ConstraintSeverity.PERSISTENT_BLOCK,
            max_soft_attempts=ruleset.settings.retries,
            soft_block_mode="cumulative",
            translator=self.translator,
            shell_tools=SHELL_TOOLS,
        )

    # ── state ─────────────────────────────────────────────────────────────────

    def restore(self, state: Optional[Dict[str, Any]]) -> None:
        state = state or {}
        self.enforcer._completed_tool_calls = list(state.get("trace") or [])
        for name, value in (state.get("engine") or {}).items():
            if name in _ENGINE_FIELDS:
                setattr(self.enforcer, name, value)

    def dump(self) -> Dict[str, Any]:
        engine = {name: getattr(self.enforcer, name, None) for name in _ENGINE_FIELDS}
        for name in ("_constraint_violations", "_soft_blocked_calls", "_block_and_warn_overrides"):
            engine[name] = list(engine[name] or [])[-_MAX_LOG:]
        return {"trace": self.enforcer._completed_tool_calls[-_MAX_TRACE:], "engine": engine}

    @property
    def trace(self) -> List[Dict[str, Any]]:
        return self.enforcer._completed_tool_calls

    # ── deciding ──────────────────────────────────────────────────────────────

    def translate(self, tool_name: str, tool_input: Dict[str, Any]) -> List[ToolCall]:
        """The structured calls a Claude Code call stands for.

        Raises:
            TranslationError: For a Bash command line cli-to-tools cannot analyse.
        """
        if tool_name in SHELL_TOOLS:
            return self.translator.translate((tool_input or {}).get(SHELL_TOOLS[tool_name]) or "")
        return [ToolCall(tool_name, dict(tool_input or {}), "", {})]

    def decide(self, tool_name: str, tool_input: Dict[str, Any], *, auto: bool = False) -> Verdict:
        from agentltl import ConstraintViolationError

        if not self.ruleset.rules:
            return Verdict()
        tool_input = tool_input or {}
        try:
            calls = self.translate(tool_name, tool_input)
        except TranslationError as exc:
            return self._unparseable(tool_input.get("command", ""), exc, auto)
        shown = [{"tool_name": c.name, "arguments": c.args} for c in calls]

        enf = self.enforcer
        enf.begin_generation()
        logged = len(enf._constraint_violations)
        blocked = len(enf._soft_blocked_calls)
        try:
            decision = enf.check(tool_name, tool_input, len(enf._completed_tool_calls) + 1)
        except ConstraintViolationError as exc:
            enf._run_status, enf._stopped_by = "completed", None
            rule = self.ruleset.get(exc.constraint_name)
            detail = self._detail(exc.constraint_name, getattr(exc, "violation_detail", ""))
            segment = self._segment(calls, exc.tool_name, exc.tool_args)
            if getattr(exc, "violation_type", "") == "SOFT_BLOCK_ESCALATION":
                attempts = enf._soft_block_counts.get(exc.constraint_name, 0)
                # the next refusal starts a fresh round of retries for Claude
                enf._soft_block_counts[exc.constraint_name] = 0
                enf._consecutive_soft_block_counts[exc.constraint_name] = 0
                reason = self._message(rule, exc.constraint_name, detail, segment, tail=(
                    f"Claude has been blocked by this rule {attempts} time(s); you decide "
                    "whether this call may run."))
                return Verdict("ask", reason, rule=exc.constraint_name, calls=shown)
            reason = self._message(rule, exc.constraint_name, detail, segment, mode="stop")
            return Verdict("stop", reason, rule=exc.constraint_name, calls=shown)

        notes = self._tolerated(enf._constraint_violations[logged:], calls)
        if decision == "allow":
            return Verdict("none", context=notes, calls=shown)

        entry = enf._soft_blocked_calls[-1] if len(enf._soft_blocked_calls) > blocked else {}
        name = entry.get("constraint_name", "")
        rule = self.ruleset.get(name)
        mode = rule.mode if rule else "block"
        detail = self._detail(name, entry.get("detail", ""))
        segment = self._segment(calls, entry.get("tool_name"), entry.get("tool_args"))
        tail = None
        if mode == "retry":
            n = enf._soft_block_counts.get(name, 0)
            tail = (f"Attempt {n} of {self.ruleset.settings.retries}. " + _TAILS["retry"])
        reason = self._message(rule, name, detail, segment, mode=mode, tail=tail)
        return Verdict("ask" if mode == "ask" else "deny", reason, context=notes, rule=name,
                       calls=shown)

    def record(self, tool_name: str, tool_input: Dict[str, Any], tool_id: str,
               result: Any) -> None:
        """Add a call that has run to the session trace."""
        tool_input = _trim(dict(tool_input or {}))
        text = result if isinstance(result, str) else ("" if result is None else str(result))
        text = text[:_MAX_STRING]
        try:
            self.enforcer.record_completed(tool_name, tool_input, tool_id, text)
        except TranslationError:
            # an unparseable command the user let through: kept, under the Claude Code name
            self.enforcer._completed_tool_calls.append(
                {"tool_name": tool_name, "arguments": tool_input, "id": tool_id, "result": text})

    # ── messages ──────────────────────────────────────────────────────────────

    def _unparseable(self, command: str, exc: Exception, auto: bool) -> Verdict:
        s = self.ruleset.settings
        how = s.unparseable_auto if auto else s.unparseable_interactive
        if how == "allow":
            return Verdict()
        related = [r.id for r in self.ruleset.rules if _shell_side(r)]
        text = (
            "This command could not be checked against the project's AGENTLTL rules: "
            f"{exc}. "
            + (f"Rules it could fall under: {', '.join(related)}. " if related else "")
            + "To have it checked, write it as plain commands joined with ;, &&, || or | "
              "(no eval, background jobs, function definitions or commands named by variables)."
        )
        if how == "ask":
            return Verdict("ask", "[AGENTLTL: not checked] " + text)
        if how == "deny":
            return Verdict("deny", "[AGENTLTL: not checked, denied] " + text)
        return Verdict("none", context="[AGENTLTL: not checked] " + text)

    def _tolerated(self, violations: List[Dict[str, Any]], calls: List[ToolCall]) -> str:
        lines = []
        for v in violations:
            if v.get("severity") != "TOLERATE":
                continue
            rule = self.ruleset.get(v.get("constraint_name", ""))
            why = f" ({rule.why})" if rule and rule.why else ""
            lines.append(f"Note: this call breaks AGENTLTL rule '{v.get('constraint_name')}'"
                         f"{why}: {self._detail(v.get('constraint_name', ''), v.get('detail', ''))}")
        return "\n".join(dict.fromkeys(lines))

    @staticmethod
    def _detail(name: str, detail: str) -> str:
        return str(detail or "").strip()

    @staticmethod
    def _segment(calls: List[ToolCall], name: Optional[str], args: Any) -> str:
        if len(calls) < 1 or not calls[0].meta:
            return ""
        for c in calls:
            if c.name == name and (args is None or c.args == args):
                return c.meta.get("source", "")
        return ""

    def _message(self, rule: Optional[Rule], name: str, detail: str, segment: str, *,
                 mode: Optional[str] = None, tail: Optional[str] = None) -> str:
        mode = mode or (rule.mode if rule else "block")
        lines = [f"[AGENTLTL] Rule '{name}' blocked this call ({mode}). Nothing was executed."]
        if rule and rule.why:
            lines.append(f"Rule: {rule.why}")
        if detail:
            lines.append(f"Problem: {detail}")
        if segment:
            lines.append(f"Blocked at: {segment}")
        if rule and rule.fix:
            lines.append(f"To comply: {rule.fix}")
        lines.append(tail or _TAILS.get(mode, ""))
        return "\n".join(line for line in lines if line)


def _shell_side(rule: Rule) -> bool:
    """Whether a rule mentions any tool a shell command can turn into."""
    return any(not (t[:1].isupper() or t.startswith("mcp__")) for t in rule.tools) or not rule.tools


def _trim(value: Any) -> Any:
    if isinstance(value, str):
        return value if len(value) <= _MAX_STRING else value[:_MAX_STRING] + "…"
    if isinstance(value, dict):
        return {k: _trim(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_trim(v) for v in value]
    return value


__all__ = ["Guard", "GuardTranslator", "Verdict", "is_auto", "lint", "translator_for", "MODES"]
