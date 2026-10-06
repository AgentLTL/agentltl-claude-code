"""The hook end to end: Claude Code event payloads in, hook JSON out."""

import json
import os
import subprocess
import sys

import pytest

from agentltl_cc import hook
from agentltl_coding import store

RULES = """
rules:
  - id: tests-before-push
    before: {first: pytest, then: git_push, since: [Edit, Write]}
    why: CI is slow.
  - id: no-secrets
    never: [Read, Edit, cat]
    where: {"*": "**/.env"}
    mode: stop
"""


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / ".git").mkdir(parents=True)
    (root / "AGENTLTL.yaml").write_text(RULES)
    return root


def event(project, name, tool=None, tool_input=None, mode="default", **extra):
    payload = {"session_id": "s1", "cwd": str(project), "permission_mode": mode,
               "hook_event_name": name, **extra}
    if tool:
        payload.update(tool_name=tool, tool_input=tool_input or {}, tool_use_id="t")
    return hook.run(payload)


def pre(project, tool, tool_input, **kw):
    out = event(project, "PreToolUse", tool, tool_input, **kw)
    return out and out["hookSpecificOutput"]


def bash(command):
    return "Bash", {"command": command}


def test_scenario(project):
    deny = pre(project, *bash("git push"))
    assert deny["permissionDecision"] == "deny"
    assert "tests-before-push" in deny["permissionDecisionReason"]

    event(project, "PostToolUse", *bash("pytest -q"), tool_response="ok")
    assert pre(project, *bash("git push")) is None              # silent: no objection

    # an edit that was proposed but never ran does not count
    assert pre(project, "Edit", {"file_path": str(project / "a.py")}) is None
    assert pre(project, *bash("git push")) is None
    event(project, "PostToolUse", "Edit", {"file_path": str(project / "a.py")})
    assert pre(project, *bash("git push"))["permissionDecision"] == "deny"

    trace = [c["tool_name"] for c in store.read("s1")["completed_tool_calls"]]
    assert trace == ["pytest", "Edit"]


def test_stop_lets_claude_explain_but_not_act_until_the_user_replies(project):
    out = event(project, "PreToolUse", "Read", {"file_path": str(project / ".env")})
    assert "continue" not in out                      # Claude keeps its turn, to explain
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "no-secrets" in out["systemMessage"] and "until you reply" in out["systemMessage"]
    locked = pre(project, *bash("ls"))                # any call, even a harmless one
    assert locked["permissionDecision"] == "deny"
    assert "stopped this session" in locked["permissionDecisionReason"]
    assert event(project, "UserPromptSubmit", prompt="ok, go on") is None
    assert pre(project, *bash("ls")) is None


def test_a_possible_match_refuses_without_stopping(project):
    out = event(project, "PreToolUse", "Bash", {"command": 'f=$(ls); cat "$f"'})
    spec = out["hookSpecificOutput"]
    assert spec["permissionDecision"] == "deny" and "systemMessage" not in out
    assert "name them explicitly" in spec["permissionDecisionReason"]
    assert pre(project, *bash("ls")) is None          # not locked


def test_unparseable_in_each_mode(project):
    assert pre(project, *bash("eval $X"))["permissionDecision"] == "ask"
    auto = pre(project, *bash("eval $X"), mode="auto")
    assert "permissionDecision" not in auto and "not checked" in auto["additionalContext"]


def test_session_start_reminds_claude_of_the_rules(project):
    out = event(project, "SessionStart", source="compact")
    context = out["hookSpecificOutput"]["additionalContext"]
    assert "tests-before-push [block]" in context and "CI is slow." in context
    assert "memory-first [warn]: before you save anything to memory" in context
    assert "3 AGENTLTL rule(s)" in context and "systemMessage" not in out


def test_broken_file_is_loud(project):
    (project / "AGENTLTL.yaml").write_text("rules: [{id: x}]")
    out = pre(project, *bash("ls"))
    assert "NO AGENTLTL rules are being enforced" in out["additionalContext"]
    start = event(project, "SessionStart")["hookSpecificOutput"]["additionalContext"]
    assert "NO AGENTLTL rules" in start and "Tell the user" in start


def test_no_rule_file_is_silent(tmp_path):
    (tmp_path / ".git").mkdir()
    assert event(tmp_path, "PreToolUse", *bash("rm -rf /")) is None


def test_internal_error_asks_instead_of_failing_open(project, monkeypatch):
    from agentltl_coding import guard

    def boom(*a, **k):
        raise RuntimeError("kaput")
    monkeypatch.setattr(guard.Guard, "decide", boom)
    out = pre(project, *bash("ls"))
    assert out["permissionDecision"] == "ask" and "kaput" in out["permissionDecisionReason"]


def test_launcher(project, tmp_path):
    payload = {"session_id": "s2", "cwd": str(project), "permission_mode": "default",
               "hook_event_name": "PreToolUse", "tool_name": "Bash",
               "tool_input": {"command": "git push"}, "tool_use_id": "t"}
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = {**os.environ, "AGENTLTL_CC_PYTHON": sys.executable,
           "CLAUDE_PROJECT_DIR": str(project)}
    done = subprocess.run([os.path.join(root, "hooks", "run"), "PreToolUse"],
                          input=json.dumps(payload), capture_output=True, text=True, env=env)
    assert done.returncode == 0
    assert json.loads(done.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_project_memory_spans_sessions(project):
    (project / "AGENTLTL.yaml").write_text(
        "rules: [{id: t, before: [pytest, git_push], scope: project}]")
    event(project, "PostToolUse", *bash("pytest"))                     # session s1
    later = {"session_id": "s2", "cwd": str(project), "permission_mode": "default",
             "hook_event_name": "PreToolUse", "tool_name": "Bash",
             "tool_input": {"command": "git push"}, "tool_use_id": "t"}
    assert hook.run(later) is None                                     # session s2
    store.reset_project(str(project))
    assert hook.run(later)["hookSpecificOutput"]["permissionDecision"] == "deny"


FINISH = """
settings: {finish_retries: 1}
rules:
  - id: tests-after-edits
    finally: {call: pytest, since: [Edit, Write]}
    fix: Run pytest.
  - id: green-before-push
    before: {first: {tool: pytest, succeeded: true}, then: git_push}
"""


def test_a_finally_rule_sends_claude_back_once_per_turn(project):
    (project / "AGENTLTL.yaml").write_text(FINISH)
    assert event(project, "Stop") is None
    event(project, "PostToolUse", "Edit", {"file_path": str(project / "a.py")}, tool_response="ok")
    out = event(project, "Stop")
    assert out["decision"] == "block" and "tests-after-edits" in out["reason"]
    assert event(project, "Stop", stop_hook_active=True) is None      # once per turn
    event(project, "UserPromptSubmit", prompt="go on")
    assert event(project, "Stop")["decision"] == "block"
    event(project, "PostToolUse", *bash("pytest -q"), tool_response="ok")
    assert event(project, "Stop") is None


def test_a_failed_command_is_recorded_as_failed(project):
    (project / "AGENTLTL.yaml").write_text(FINISH)
    event(project, "PostToolUseFailure", *bash("pytest -q"), error="Exit code 1")
    calls = store.read("s1")["completed_tool_calls"]
    assert calls[-1]["tool_name"] == "pytest" and calls[-1]["status"] == 1
    assert pre(project, *bash("git push"))["permissionDecision"] == "deny"
    event(project, "PostToolUse", *bash("pytest -q"), tool_response="3 passed")
    assert pre(project, *bash("git push")) is None
