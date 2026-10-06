"""
agentltl_cc/hook.py – the Claude Code hook: ``python -m agentltl_cc.hook`` reads the
event JSON on stdin and prints the hook's JSON answer.

    SessionStart   remind Claude of the rules (also after a compaction); report file errors
    PreToolUse     deny / ask / stop when a call breaks a rule, else stay silent
    UserPromptSubmit  lift a stop: after a `stop` rule fires, every tool call is refused until
                   the user replies, so Claude can explain but not act; and let `finally`
                   rules send Claude back again in this new turn
    PostToolUse    add the call that ran to the session and project traces (status 0); warn
                   when its output contains a credential (settings.scan_output)
    PostToolUseFailure  the same for a call that failed (a command's non-zero exit: status 1)
    Stop           Claude is about to finish: while a `finally` rule is unmet, send it back
                   with what is missing (at most settings.finish_retries times per turn)

Without an AGENTLTL.yaml (project or ``~/.claude``) it exits at once. It never approves a
call: silence leaves the decision to Claude Code's permission flow. Any internal error
during PreToolUse turns into "ask", so a broken guard is visible instead of failing open.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional

from . import CLAUDE_CODE  # noqa: F401  (configures the harness)


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
        from agentltl_coding import Session
        cwd = payload.get("cwd") or os.getcwd()
        session = Session(cwd, os.environ.get("CLAUDE_PROJECT_DIR") or cwd,
                          payload.get("session_id"))
        if not session.files:
            return None
        return _handle(event, payload, session)
    except Exception as exc:  # the guard must not fail open silently
        return _failure(event, exc)


def _handle(event: str, payload: Dict[str, Any], session: Any) -> Optional[Dict[str, Any]]:
    from agentltl_coding.guard import is_auto
    from agentltl_coding.rules import RuleFileError

    if event == "UserPromptSubmit":          # the user replied: lift a stop; finally rules
        session.prompt()                     # may send Claude back again
        return None
    try:
        session.ruleset
    except RuleFileError as exc:
        return _broken_file(event, exc.problems)

    if event == "SessionStart":
        return _session_start(session)
    tool, tool_input = payload.get("tool_name", ""), payload.get("tool_input") or {}
    if event in ("PostToolUse", "PostToolUseFailure"):
        failed = event == "PostToolUseFailure"
        found = session.post(tool, tool_input, payload.get("tool_use_id", ""),
                             payload.get("error") if failed else payload.get("tool_response"),
                             status=1 if failed else 0)
        return _credentials(event, found)
    if event == "Stop":
        verdict = session.finish()
        return {"decision": "block", "reason": verdict.reason} if verdict.action == "block" \
            else None
    if event == "PreToolUse":
        return _pre_tool_use(session.pre(tool, tool_input,
                                         auto=is_auto(payload.get("permission_mode"))))
    return None


def _credentials(event: str, found: Any) -> Optional[Dict[str, Any]]:
    if not found:
        return None
    user, agent = found
    return {"systemMessage": user,
            "hookSpecificOutput": {"hookEventName": event, "additionalContext": agent}}


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


def _session_start(session: Any) -> Optional[Dict[str, Any]]:
    # Claude Code does not show a SessionStart hook's systemMessage, so the user sees the
    # rules in force through `agentltl statusline`; this only tells Claude.
    text = session.start()
    if text is None:
        return None
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}


def _broken_file(event: str, problems: List[str]) -> Optional[Dict[str, Any]]:
    from agentltl_coding.session import broken
    text = broken(problems)
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
