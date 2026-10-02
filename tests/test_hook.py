"""The hook end to end: Claude Code event payloads in, hook JSON out."""

import json
import os
import subprocess
import sys

import pytest

from agentltl_guard import hook, store

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

    trace = [c["tool_name"] for c in store.read("s1")["trace"]]
    assert trace == ["pytest", "Edit"]


def test_stop_halts_claude(project):
    out = event(project, "PreToolUse", "Read", {"file_path": str(project / ".env")})
    assert out["continue"] is False
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_unparseable_in_each_mode(project):
    assert pre(project, *bash("eval $X"))["permissionDecision"] == "ask"
    auto = pre(project, *bash("eval $X"), mode="auto")
    assert "permissionDecision" not in auto and "not checked" in auto["additionalContext"]


def test_session_start_reminds_claude_of_the_rules(project):
    out = event(project, "SessionStart", source="compact")
    context = out["hookSpecificOutput"]["additionalContext"]
    assert "tests-before-push [block]" in context and "CI is slow." in context
    assert "2 rule(s)" in out["systemMessage"]


def test_broken_file_is_loud(project):
    (project / "AGENTLTL.yaml").write_text("rules: [{id: x}]")
    out = pre(project, *bash("ls"))
    assert "NO AGENTLTL rules are being enforced" in out["additionalContext"]
    assert "systemMessage" in event(project, "SessionStart")


def test_no_rule_file_is_silent(tmp_path):
    (tmp_path / ".git").mkdir()
    assert event(tmp_path, "PreToolUse", *bash("rm -rf /")) is None


def test_internal_error_asks_instead_of_failing_open(project, monkeypatch):
    from agentltl_guard import guard

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
    env = {**os.environ, "AGENTLTL_GUARD_PYTHON": sys.executable,
           "CLAUDE_PROJECT_DIR": str(project)}
    done = subprocess.run([os.path.join(root, "hooks", "run"), "PreToolUse"],
                          input=json.dumps(payload), capture_output=True, text=True, env=env)
    assert done.returncode == 0
    assert json.loads(done.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
