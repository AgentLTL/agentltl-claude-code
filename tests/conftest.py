import pytest

import agentltl_cc  # noqa: F401  (configures the Claude Code harness)

from agentltl_coding.guard import Guard
from agentltl_coding.pattern import Paths
from agentltl_coding.rules import loads


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """No user-level rule file, and session state under tmp."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENTLTL_CC_STATE", str(tmp_path / "state"))
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)


def guard_for(text, cwd="/proj"):
    paths = Paths(cwd, "/proj")
    g = Guard(loads(text, paths), paths)
    g.restore({})
    return g


def run(guard, *steps, auto=False):
    """Decide each step; allowed steps are recorded as executed. Returns the actions."""
    out = []
    for step in steps:
        tool, tool_input = ("Bash", {"command": step}) if isinstance(step, str) else step
        v = guard.decide(tool, tool_input, auto=auto)
        out.append(v.action)
        if v.action == "none":
            guard.record(tool, tool_input, "id", "")
    return out
