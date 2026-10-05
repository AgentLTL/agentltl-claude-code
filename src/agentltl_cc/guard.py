"""
agentltl_cc/guard.py – decide one Claude Code tool call against the rules.

    guard = Guard(ruleset, Paths(cwd, root))
    guard.restore(session_state, project_state)   # traces + enforcer counters
    verdict = guard.decide("Bash", {"command": "git push"}, auto=False)
    verdict.action                       # "none" | "deny" | "ask" | "stop"
    ...after the call ran...
    guard.record("Bash", {"command": "pytest"}, "toolu_1", "3 passed")
    session_state, project_state = guard.dump(), guard.dump_project()

Each rule reads one memory (its ``scope``): the session trace (calls made in this Claude Code
session) or the project trace (every call made in this project, across sessions). Both
record every call that runs; each scope has its own enforcer, and the stricter verdict wins.

``Bash`` calls are translated by cli-to-tools into the structured calls of the command line
(``git commit -m x && git push`` → ``git_commit``, ``git_push``) and checked all or nothing.
Every other tool is checked under its Claude Code name with its input as arguments.

The guard never approves anything: "none" means it has no objection and Claude Code's own
permission flow (your settings, auto mode) still decides.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from cli_to_tools import SpecRegistry, ToolCall, TranslationError, Translator
from cli_to_tools.agentltl import CliConstraintEnforcer

from .match import PATH_KEYS, UNKNOWN_PATHS, Paths, normalize_paths, unknown_path_keys
from .rules import MODES, SCOPES, Rule, RuleSet

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
_RANK = {"none": 0, "ask": 1, "deny": 2, "stop": 3}

_TAILS = {
    "block": "This rule cannot be overridden by you. Do something that satisfies it instead, "
             "or explain the situation to the user.",
    "warn": "This is a warning. If you are sure the call is right, you may override the rule by "
            "repeating exactly the same call as your next action; otherwise comply.",
    "retry": "Change your approach to satisfy the rule. If you keep getting blocked, the user "
             "will be asked to decide.",
    "stop": "This rule stops the session. Stop working and tell the user what you were trying "
            "to do and why.",
}
# Told to Claude alongside a prompt the user answers (mode: ask, or retry escalating).
_ASKED = ("AGENTLTL rule '{name}' asked the user to approve this call. If they decline, do not "
          "retry it or work around the rule; ask the user what they want instead.")


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
    """cli-to-tools translator with what file rules need on top:

    - ``redirect_to`` / ``redirect_from``: files a redirection writes (``> f``, ``>> f``,
      ``2> f``, ``&> f``, ``cat <<EOF > f``) or reads (``< f``); ``overwrite_to``: the
      ones that truncate the file first (``>``, ``&>``, not ``>>``, nor ``/dev/null``);
    - path arguments made absolute (see :func:`normalize_paths`);
    - ``unknown_paths``: the call's files are only known at run time (``xargs rm``,
      ``find -exec rm {}``, ``rm $UNSET``);
    - for ``patch`` / ``git apply``, the files the diff modifies, as ``paths``;
    - for ``curl -O``, the file it writes, as ``output``.

    Relative paths are resolved from the directory the command line has moved to: in
    ``cd sub && rm a``, ``a`` is ``sub/a``. A ``cd`` lasts until the end of its subshell (or
    ``bash -c``), and one in a pipeline changes nothing (each part runs in a subshell). After a
    ``cd`` to a directory only known at run time (``cd $D``, ``cd -``, ``popd``), relative paths
    are unknown too.
    """

    paths: Optional[Paths] = None

    def translate(self, command: str) -> List[ToolCall]:
        self._dirs: Dict[int, Optional[str]] = {}    # depth -> directory (None: unknown)
        return super().translate(command)

    def _call(self, node: Any, command: str, call_id: str, index: int) -> ToolCall:
        call = super()._call(node, command, call_id, index)
        if self.paths is None:
            return self._finish(call, node, self.paths, False)
        dirs = getattr(self, "_dirs", {})
        depth = getattr(node, "depth", 0) or 0
        for d in [d for d in dirs if d > depth]:
            del dirs[d]                                 # left that subshell
        here = dirs[max(dirs)] if dirs else self.paths.cwd
        paths = Paths(here or self.paths.cwd, self.paths.root)
        call = self._finish(call, node, paths, here is None)
        if call.name in ("cd", "pushd", "popd") and not node.pipeline:
            dirs[depth] = _cd_target(call, here)
        return call

    def _finish(self, call: ToolCall, node: Any, paths: Optional[Paths],
                lost: bool) -> ToolCall:
        writes, reads, truncates = _redirect_files(node.redirects)
        if writes:
            call.args["redirect_to"] = writes
        if truncates:
            call.args["overwrite_to"] = truncates
        if reads:
            call.args["redirect_from"] = reads
        if paths is not None:
            relative = _relative_path_keys(call.args) if lost else []
            if call.name in ("patch", "git_apply"):
                _patch_targets(call, paths)
            if call.name == "curl" and call.args.get("remote_name") and not call.args.get("output"):
                _curl_output(call)
            call.args = normalize_paths(call.args, paths)
        else:
            relative = []
        if node.wrapper == "xargs":
            call.args[UNKNOWN_PATHS] = True
        elif not call.args.get(UNKNOWN_PATHS):
            unknown = list(dict.fromkeys(unknown_path_keys(call.args) + relative))
            if unknown:
                call.args[UNKNOWN_PATHS] = unknown
        return call


def _cd_target(call: ToolCall, here: Optional[str]) -> Optional[str]:
    """The directory a ``cd`` / ``pushd`` / ``popd`` call moves to; None when only known at
    run time."""
    if call.name == "popd":
        return None
    if call.name == "pushd":
        words = [w for w in call.args.get("argv") or [] if not w.startswith("-")]
        target = words[0] if words else None
        if target is None or target.startswith("+"):
            return None                     # swaps with the directory stack
    else:
        target = call.args.get("path")
        if not target:
            return os.path.expanduser("~")
    if target == "-" or call.args.get(UNKNOWN_PATHS) or "$" in target or "`" in target:
        return None
    target = os.path.expanduser(target)
    if os.path.isabs(target):
        return os.path.normpath(target)
    return None if here is None else os.path.normpath(os.path.join(here, target))


def _relative_path_keys(args: Dict[str, Any]) -> List[str]:
    """Path arguments holding a relative path: unknown after a ``cd $D``."""
    keys = []
    for key in PATH_KEYS:
        value = args.get(key)
        values = value if isinstance(value, list) else [value]
        if any(isinstance(v, str) and v and not v.startswith(("/", "~", "$", "-"))
               and "://" not in v for v in values):
            keys.append(key)
    return keys


def _redirect_files(redirects: List[Dict[str, str]]) -> "tuple[List[str], List[str], List[str]]":
    writes: List[str] = []
    reads: List[str] = []
    truncates: List[str] = []
    for r in redirects:
        op, target = r.get("op", ""), r.get("target", "")
        if not target:
            continue
        if op in (">&", "<&") and (target.isdigit() or target == "-"):
            continue                       # 2>&1: copies a descriptor, no file
        if op in (">", ">>", ">|", "&>", "&>>", ">&"):
            writes.append(target)
            if op in (">", ">|", "&>", ">&") and not _is_device(target):
                truncates.append(target)
        elif op == "<":
            reads.append(target)
    return writes, reads, truncates


_DEVICES = ("/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty")


def _is_device(target: str) -> bool:
    """Writing to these discards or shows the output; it overwrites no file."""
    return target in _DEVICES or target.startswith("/dev/fd/")


def _patch_targets(call: ToolCall, paths: Paths) -> None:
    """Read the diff a ``patch`` / ``git apply`` will apply and list the files it touches."""
    a = call.args
    diffs = [a.get("input"), a.get("patchfile"), *(a.get("patches") or []),
             *(a.get("redirect_from") or [])]
    diffs = [d for d in diffs if d]
    strip = a.get("strip")
    strips = [int(strip)] if str(strip or "").isdigit() else (
        [1] if call.name == "git_apply" else [0, 1])
    found: List[str] = []
    readable = bool(diffs)
    for diff in diffs:
        path = os.path.join(paths.cwd or os.getcwd(), os.path.expanduser(diff))
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read(2_000_000)
        except OSError:
            readable = False
            continue
        for name in _diff_files(text):
            for n in strips:
                parts = name.split("/")
                if len(parts) > n:
                    found.append("/".join(parts[n:]))
    if found:
        a["paths"] = list(dict.fromkeys(found))
    if not readable:
        a[UNKNOWN_PATHS] = True


def _diff_files(text: str) -> List[str]:
    names: List[str] = []
    for line in text.splitlines():
        if line.startswith(("--- ", "+++ ")):
            name = line[4:].split("\t")[0].strip()
        elif line.startswith(("rename to ", "rename from ", "copy to ")):
            name = line.split(" ", 2)[2].strip()
        else:
            continue
        if name and name != "/dev/null":
            names.append(name)
    return list(dict.fromkeys(names))


def _curl_output(call: ToolCall) -> None:
    from urllib.parse import urlparse
    urls = call.args.get("urls") or []
    names = [os.path.basename(urlparse(u).path) for u in urls]
    names = [n for n in names if n]
    if names:
        out_dir = call.args.get("output_dir") or ""
        call.args["output"] = [os.path.join(out_dir, n) for n in names]


def translator_for(ruleset: RuleSet) -> GuardTranslator:
    registry = SpecRegistry()
    if ruleset.tool_specs:   # extends the bundled spec of the same command unless `extend: false`
        registry.load_dict(ruleset.tool_specs, extend=True)
    return GuardTranslator(registry)


# runners whose subcommands cli-to-tools does not split into tools (`make test` -> make, argv)
_RUNNERS = ("make", "npm", "yarn", "pnpm", "npx", "cargo", "go", "uv", "poetry", "just", "bun")
_ANY_TOOL_ARGS = ("*", "redirect_to", "overwrite_to", "redirect_from", "extra_args")


def lint(ruleset: RuleSet, registry: Optional[SpecRegistry] = None) -> List[str]:
    """Warnings for targets that can never match: unknown tools or argument names.

    The rule file is valid without them; these catch the rule that silently never fires
    (``with: {repository: origin}`` on ``git_push``, whose argument is ``remote``) or, for
    ``require``, fires on every call.
    """
    registry = registry or translator_for(ruleset).registry
    props = {s["name"]: s.get("parameters", {}).get("properties", {})
             for s in registry.tool_schemas()}
    schemas = {name: set(p) for name, p in props.items()}
    out: List[str] = []
    for rule in ruleset.rules:
        if rule.kind == "before" and rule.targets:
            for target in _flatten(rule.targets[:1]):
                for tool in target.tools:
                    lists = sorted(arg for arg in target.variables
                                   if props.get(tool, {}).get(arg, {}).get("type") == "array")
                    if lists:
                        out.append(f"{rule.id}: {tool}.{lists[0]} is a list, and a $variable "
                                   "on the 'first' side is compared with the whole list, so "
                                   f"`{tool.replace('_', ' ')} a b` never matches one file. "
                                   "Use a single-valued argument (e.g. Read's file_path).")
        for target in _flatten(rule.targets):
            keys = set(target.with_) | set(target.where) | set(target.variables)
            for tool in target.tools:
                if tool[:1].isupper() or tool.startswith("mcp__") or "*" in tool:
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
        self.translator.paths = self.paths
        self.enforcers = {
            scope: CliConstraintEnforcer(
                constraints=ruleset.constraints(scope),
                constraint_severities=ruleset.severities(scope),
                default_severity=ConstraintSeverity.PERSISTENT_BLOCK,
                max_soft_attempts=ruleset.settings.retries,
                soft_block_mode="cumulative",
                translator=self.translator,
                shell_tools=SHELL_TOOLS,
            )
            for scope in SCOPES
        }

    # ── state ─────────────────────────────────────────────────────────────────

    def restore(self, session: Optional[Dict[str, Any]],
                project: Optional[Dict[str, Any]] = None) -> None:
        for scope, state in (("session", session), ("project", project)):
            enf, state = self.enforcers[scope], state or {}
            enf._completed_tool_calls = list(state.get("trace") or [])
            for name, value in (state.get("engine") or {}).items():
                if name in _ENGINE_FIELDS:
                    setattr(enf, name, value)

    def dump(self, scope: str = "session") -> Dict[str, Any]:
        enf = self.enforcers[scope]
        engine = {name: getattr(enf, name, None) for name in _ENGINE_FIELDS}
        for name in ("_constraint_violations", "_soft_blocked_calls", "_block_and_warn_overrides"):
            engine[name] = list(engine[name] or [])[-_MAX_LOG:]
        return {"trace": enf._completed_tool_calls[-_MAX_TRACE:], "engine": engine}

    def dump_project(self) -> Dict[str, Any]:
        return self.dump("project")

    @property
    def trace(self) -> List[Dict[str, Any]]:
        return self.enforcers["session"]._completed_tool_calls

    @property
    def project_trace(self) -> List[Dict[str, Any]]:
        return self.enforcers["project"]._completed_tool_calls

    # ── deciding ──────────────────────────────────────────────────────────────

    def translate(self, tool_name: str, tool_input: Dict[str, Any]) -> List[ToolCall]:
        """The structured calls a Claude Code call stands for.

        Raises:
            TranslationError: For a Bash command line cli-to-tools cannot analyse.
        """
        if tool_name in SHELL_TOOLS:
            return self.translator.translate((tool_input or {}).get(SHELL_TOOLS[tool_name]) or "")
        return [ToolCall(tool_name, self._input(tool_name, tool_input), "", {})]

    def _input(self, tool_name: str, tool_input: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """The arguments rules see: path arguments of non-shell tools made absolute."""
        tool_input = dict(tool_input or {})
        return tool_input if tool_name in SHELL_TOOLS else normalize_paths(tool_input, self.paths)

    def decide(self, tool_name: str, tool_input: Dict[str, Any], *, auto: bool = False) -> Verdict:

        if not self.ruleset.rules:
            return Verdict()
        tool_input = tool_input or {}
        try:
            calls = self.translate(tool_name, tool_input)
        except TranslationError as exc:
            return self._unparseable(tool_input.get("command", ""), exc, auto)
        shown = [{"tool_name": c.name, "arguments": c.args} for c in calls]
        verdicts = [self._decide_in(scope, tool_name, tool_input, calls, shown)
                    for scope in SCOPES if self.ruleset.scoped(scope)]
        verdict = max(verdicts, key=lambda v: _RANK[v.action])
        notes = "\n".join(v.context for v in verdicts if v.context)
        verdict.context = notes
        return verdict

    def _decide_in(self, scope: str, tool_name: str, tool_input: Dict[str, Any],
                   calls: List[ToolCall], shown: List[Dict[str, Any]]) -> Verdict:
        from agentltl import ConstraintViolationError

        enf = self.enforcers[scope]
        enf.begin_generation()
        logged = len(enf._constraint_violations)
        blocked = len(enf._soft_blocked_calls)
        try:
            decision = enf.check(tool_name, self._input(tool_name, tool_input),
                                 len(enf._completed_tool_calls) + 1)
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
                reason = self._ask_message(rule, exc.constraint_name, detail, segment, intro=(
                    f"Claude was refused {attempts - 1} time(s) by this rule and is trying again."))
                return Verdict("ask", reason, context=_ASKED.format(name=exc.constraint_name),
                               rule=exc.constraint_name, calls=shown)
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
        if mode == "ask":
            reason = self._ask_message(rule, name, detail, segment)
            notes = "\n".join(n for n in (_ASKED.format(name=name), notes) if n)
            return Verdict("ask", reason, context=notes, rule=name, calls=shown)
        reason = self._message(rule, name, detail, segment, mode=mode, tail=tail)
        return Verdict("deny", reason, context=notes, rule=name, calls=shown)

    def record(self, tool_name: str, tool_input: Dict[str, Any], tool_id: str,
               result: Any) -> None:
        """Add a call that has run to the session and project traces."""
        tool_input = _trim(self._input(tool_name, tool_input))
        text = result if isinstance(result, str) else ("" if result is None else str(result))
        text = text[:_MAX_STRING]
        for enf in self.enforcers.values():
            try:
                enf.record_completed(tool_name, tool_input, tool_id, text)
            except TranslationError:
                # an unparseable command the user let through: kept under the Claude Code name
                enf._completed_tool_calls.append({"tool_name": tool_name, "arguments": tool_input,
                                                  "id": tool_id, "result": text})

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
        return _plain(str(detail or "").strip())

    @staticmethod
    def _segment(calls: List[ToolCall], name: Optional[str], args: Any) -> str:
        if len(calls) < 1 or not calls[0].meta:
            return ""
        for c in calls:
            if c.name == name and (args is None or c.args == args):
                return c.meta.get("source", "")
        return ""

    @staticmethod
    def _ask_message(rule: Optional[Rule], name: str, detail: str, segment: str,
                     intro: str = "") -> str:
        """The permission prompt the user reads: what the rule protects, and the question."""
        lines = [f"AgentLTL: this call breaks the rule '{name}'.", intro]
        if rule and rule.why:
            lines.append(f"Why the rule exists: {rule.why}")
        if detail:
            lines.append(f"What breaks it: {detail}")
        if segment:
            lines.append(f"Command: {segment}")
        lines.append("Claude cannot override this rule. Allow this call anyway?")
        return "\n".join(line for line in lines if line)

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


_FORALL = re.compile(r"^∀(\w+): failed for \1=('(?:[^'\\]|\\.)*'|\S+)\.\s*")
_CALLED_BUT = re.compile(r'^"([^"]+)" was called but not with the expected arguments (\{.*\})\.?$', re.S)
_NEVER = re.compile(r'^"([^"]+)" was never called\.?$')


def _plain(detail: str) -> str:
    """AgentLTL's explanation of a failed `before` with $variables, in plain words:
    "for chart='web', v='2': no earlier helm_upgrade had namespace='staging', ...". Any
    other text is returned unchanged."""
    bound: List[str] = []
    while True:
        m = _FORALL.match(detail)
        if not m:
            break
        bound.append(f"{m.group(1)}={m.group(2)}")
        detail = detail[m.end():]
    if not bound:
        return detail
    m = _CALLED_BUT.match(detail)
    if m:
        try:
            import ast
            args = ast.literal_eval(m.group(2))
            shown = ", ".join(f"{k}={v!r}" for k, v in args.items())
        except (ValueError, SyntaxError):
            shown = m.group(2)
        detail = f"no earlier {m.group(1)} call had {shown}."
    else:
        m = _NEVER.match(detail)
        if m:
            detail = f"{m.group(1)} was never called."
    return f"for {', '.join(bound)}: {detail}"


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
