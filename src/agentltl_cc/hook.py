"""
agentltl_cc/hook.py – the Claude Code hook: ``python -m agentltl_cc.hook`` reads the
event JSON on stdin and prints the hook's JSON answer.

    SessionStart   remind Claude of the rules (also after a compaction); report file errors
    PreToolUse     deny / ask / stop when a call breaks a rule, else stay silent
    UserPromptSubmit  lift a stop: after a `stop` rule fires, every tool call is refused until
                   the user replies, so Claude can explain but not act
    PostToolUse    add the call that ran to the session and project traces; warn when its
                   output contains a credential (settings.scan_output)

Without an AGENTLTL.yaml (project or ``~/.claude``) it exits at once. It never approves a
call: silence leaves the decision to Claude Code's permission flow. Any internal error
during PreToolUse turns into "ask", so a broken guard is visible instead of failing open.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    out = run(payload)
    if out:
        sys.stdout.write(json.dumps(out))
    return 0


def run(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The hook's answer to one event, or None for no output."""
    event = payload.get("hook_event_name", "")
    try:
        from .rules import rule_files
        cwd = payload.get("cwd") or os.getcwd()
        project = os.environ.get("CLAUDE_PROJECT_DIR") or cwd
        files = rule_files(cwd, project)
        if not files:
            return None
        return _handle(event, payload, files, cwd, project)
    except Exception as exc:  # the guard must not fail open silently
        return _failure(event, exc)


def _handle(event: str, payload: Dict[str, Any], files: List[str], cwd: str,
            project: str) -> Optional[Dict[str, Any]]:
    logging.getLogger("agentltl").setLevel(logging.ERROR)
    logging.getLogger("cli_to_tools").setLevel(logging.ERROR)
    from . import store
    from .guard import Guard, Verdict, is_auto
    from .match import Paths
    from .rules import RuleFileError, load

    sid = payload.get("session_id") or "default"
    if event == "UserPromptSubmit":          # the user replied: lift a stop, even if the rule
        with store.locked(sid) as state:     # file has since become unreadable
            state.pop("stopped", None)
        return None
    try:
        ruleset = load(files, Paths(cwd, project))
    except RuleFileError as exc:
        return _broken_file(event, exc.problems)

    if event == "SessionStart":
        return _session_start(ruleset)
    if event not in ("PreToolUse", "PostToolUse"):
        return None

    tool, tool_input = payload.get("tool_name", ""), payload.get("tool_input") or {}
    guard = Guard(ruleset, Paths(cwd, project))
    with store.locked(sid) as state, store.locked_project(project) as project_state:
        guard.restore(state, project_state)
        if event == "PostToolUse":
            guard.record(tool, tool_input, payload.get("tool_use_id", ""),
                         payload.get("tool_response"))
            verdict = None
        elif state.get("stopped"):
            verdict = Verdict("deny", _STILL_STOPPED.format(**state["stopped"]),
                              rule=state["stopped"].get("rule"))
        else:
            verdict = guard.decide(tool, tool_input, auto=is_auto(payload.get("permission_mode")))
            if verdict.action == "stop":
                state["stopped"] = {"rule": verdict.rule or "?"}
        state.update(guard.dump())
        state["project_dir"] = project
        project_state.update(guard.dump_project())
        if verdict is not None and verdict.action != "none":
            state.setdefault("decisions", []).append(
                {"tool": tool, "input": tool_input, "action": verdict.action, "rule": verdict.rule})
            state["decisions"] = state["decisions"][-200:]
    if event == "PostToolUse":
        return _scan(ruleset, tool, payload.get("tool_response"))
    return None if verdict is None else _pre_tool_use(verdict)


_STILL_STOPPED = (
    "[AGENTLTL] Rule '{rule}' stopped this session: every tool call is refused until the user "
    "replies. Nothing was executed. Don't try another command or a workaround. Tell the user "
    "what you were trying to do, why, and what you need from them, then end your turn.")


def _scan(ruleset: Any, tool: str, response: Any) -> Optional[Dict[str, Any]]:
    if not ruleset.settings.scan_output or response is None:
        return None
    from .scan import credential_kinds, report
    kinds = credential_kinds(response)
    return report(tool, kinds) if kinds else None


def _pre_tool_use(verdict: Any) -> Optional[Dict[str, Any]]:
    spec: Dict[str, Any] = {"hookEventName": "PreToolUse"}
    out: Dict[str, Any] = {"hookSpecificOutput": spec}
    if verdict.action in ("deny", "ask", "stop"):
        spec["permissionDecision"] = "ask" if verdict.action == "ask" else "deny"
        spec["permissionDecisionReason"] = verdict.reason
    if verdict.action == "stop":
        # Not `continue: false`: that would end Claude's turn before it could explain. Claude
        # keeps the turn to tell the user what it was doing; every tool call is refused until
        # the user replies (see UserPromptSubmit).
        out["systemMessage"] = (f"AgentLTL: rule '{verdict.rule}' stopped Claude. It can explain, "
                                "but no tool runs until you reply.")
    if verdict.context:
        spec["additionalContext"] = verdict.context
    return out if len(spec) > 1 or "continue" in out else None


def _session_start(ruleset: Any) -> Dict[str, Any]:
    # Claude Code does not show a SessionStart hook's systemMessage, so the user sees the
    # rules in force through `agentltl statusline`; this only tells Claude.
    n = len(ruleset.rules)
    if not ruleset.settings.announce:
        return None
    lines = [
        f"This project enforces {n} AGENTLTL rule(s) on every tool call, shell commands "
        "included (each command line is checked as the sequence of commands it runs). "
        "A call that breaks a rule is refused with the reason; follow it rather than "
        "working around it. Rules:",
    ]
    for r in ruleset.rules:
        if r.id == "memory-first" and r.kind == "never":
            lines.append(f"- memory-first [{r.mode}]: before you save anything to memory "
                         "(CLAUDE.md, CLAUDE.local.md, .claude/rules/, auto memory), ask whether "
                         "it is a rule about tool calls or commands. If it is, add it to "
                         "AGENTLTL.yaml with the /agentltl:rules skill instead: rules there are "
                         "enforced, memory can be forgotten.")
            continue
        why = f" — {r.why}" if r.why else ""
        memory = ", whole project" if r.scope == "project" else ""
        lines.append(f"- {r.id} [{r.mode}{memory}]: {r.summary}{why}")
    return {
        "hookSpecificOutput": {"hookEventName": "SessionStart",
                               "additionalContext": "\n".join(lines)},
    }


def _broken_file(event: str, problems: List[str]) -> Optional[Dict[str, Any]]:
    text = ("AGENTLTL.yaml has errors, so NO AGENTLTL rules are being enforced until it is "
            "fixed:\n" + "\n".join(f"- {p}" for p in problems))
    if event == "SessionStart":
        return {"hookSpecificOutput": {"hookEventName": "SessionStart",
                                       "additionalContext": text + "\nTell the user at the "
                                       "start of your first reply."}}
    if event == "PreToolUse":
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                       "additionalContext": text + "\nTell the user."}}
    return None


def _failure(event: str, exc: Exception) -> Optional[Dict[str, Any]]:
    text = f"AgentLTL guard error ({type(exc).__name__}: {exc})"
    if event == "PreToolUse":
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "ask",
            "permissionDecisionReason": text + "; this call was NOT checked against the rules."}}
    if event == "SessionStart":
        return {"hookSpecificOutput": {"hookEventName": "SessionStart",
                                       "additionalContext": text + "; NO rules are enforced. "
                                       "Tell the user at the start of your first reply."}}
    return {"systemMessage": text}


if __name__ == "__main__":
    sys.exit(main())
