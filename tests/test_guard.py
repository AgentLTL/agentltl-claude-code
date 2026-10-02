"""Escalation modes, unparseable commands, and state carried between hook processes."""

import json

import pytest

from agentltl_cc.guard import Guard
from agentltl_cc.match import Paths
from agentltl_cc.rules import RuleFileError, loads

from .conftest import guard_for, run

NEVER_RM = "rules: [{{id: r, never: rm, why: keep files, fix: move them to trash/, mode: {}}}]"


class TestModes:
    def test_block_denies_every_time(self):
        g = guard_for(NEVER_RM.format("block"))
        assert run(g, "rm a", "rm a", "rm a") == ["deny"] * 3

    def test_warn_lets_claude_insist_with_the_exact_call(self):
        g = guard_for(NEVER_RM.format("warn"))
        assert run(g, "rm a", "rm b", "rm b", "rm c") == ["deny", "deny", "none", "deny"]

    def test_warn_override_needs_the_very_next_call(self):
        g = guard_for(NEVER_RM.format("warn"))
        assert run(g, "rm a", "ls", "rm a") == ["deny", "none", "deny"]

    def test_retry_escalates_to_the_user_then_starts_over(self):
        g = guard_for("settings: {retries: 2}\n" + NEVER_RM.format("retry"))
        assert run(g, "rm a", "rm b", "rm c", "rm d") == ["deny", "ask", "deny", "ask"]

    def test_ask(self):
        assert run(guard_for(NEVER_RM.format("ask")), "rm a", "rm a") == ["ask", "ask"]

    def test_stop(self):
        assert run(guard_for(NEVER_RM.format("stop")), "rm a") == ["stop"]

    def test_log_allows_and_tells_claude(self):
        g = guard_for(NEVER_RM.format("log"))
        v = g.decide("Bash", {"command": "ls && rm a"})
        assert v.action == "none" and "breaks AGENTLTL rule 'r' (keep files)" in v.context

    def test_strongest_rule_decides(self):
        # a warn override must not slip past a block rule the same call breaks
        g = guard_for("""
rules:
  - {id: soft, never: rm, mode: warn}
  - {id: hard, never: rm, with: {recursive: true}, mode: block}
""")
        assert run(g, "rm -r a", "rm -r a") == ["deny", "deny"]
        assert g.decide("Bash", {"command": "rm -r a"}).rule == "hard"

    def test_message(self):
        v = guard_for(NEVER_RM.format("block")).decide(
            "Bash", {"command": "ls && rm -f a.txt | cat"})
        assert v.reason.splitlines() == [
            "[AGENTLTL] Rule 'r' blocked this call (block). Nothing was executed.",
            "Rule: keep files",
            "Problem: rm is not allowed: it matches rm.",
            "Blocked at: rm -f a.txt",
            "To comply: move them to trash/",
            "This rule cannot be overridden by you. Do something that satisfies it instead, "
            "or explain the situation to the user.",
        ]
        assert [c["tool_name"] for c in v.calls] == ["ls", "rm", "cat"]


class TestUnparseable:
    RULES = "rules: [{id: r, never: rm}, {id: e, never: Edit, where: {file_path: '*.lock'}}]"

    def test_interactive_asks_and_auto_notes(self):
        g = guard_for(self.RULES)
        cmd = {"command": "eval $CMD"}
        asked = g.decide("Bash", cmd)
        noted = g.decide("Bash", cmd, auto=True)
        assert asked.action == "ask" and "could not be checked" in asked.reason
        assert "Rules it could fall under: r." in asked.reason   # not the Edit-only rule
        assert noted.action == "none" and "could not be checked" in noted.context

    def test_settings(self):
        g = guard_for("settings: {unparseable: {interactive: deny, auto: allow}}\n" + self.RULES)
        assert g.decide("Bash", {"command": "eval x"}).action == "deny"
        assert g.decide("Bash", {"command": "eval x"}, auto=True).context == ""

    def test_recorded_under_the_claude_code_name_when_let_through(self):
        g = guard_for(self.RULES)
        g.record("Bash", {"command": "eval x"}, "t", "")
        assert g.trace[-1]["tool_name"] == "Bash"

    def test_no_rules_no_opinion(self):
        assert guard_for("rules: []").decide("Bash", {"command": "eval x"}).action == "none"


def test_state_round_trips_through_json():
    text = "rules: [{id: t, before: [pytest, git_push]}, {id: w, never: rm, mode: warn}]"
    rs = loads(text)

    def fresh(state):
        g = Guard(rs, Paths("/proj", "/proj"))
        g.restore(json.loads(json.dumps(state)))
        return g

    g = fresh({})
    assert run(g, "pytest", "rm x") == ["none", "deny"]
    g = fresh(g.dump())   # a new hook process
    assert run(g, "rm x", "git push") == ["none", "none"]
    assert [c["tool_name"] for c in g.trace] == ["pytest", "rm", "git_push"]


def test_redirections_are_visible():
    g = guard_for("rules: [{id: r, never: {tool: [echo, Write], where: {'*': '**/.env'}}}]")
    assert run(g, "echo x > a.txt", "echo x >> .env", "echo x 2> .env") == [
        "none", "deny", "none"]


def test_large_inputs_are_trimmed_in_the_trace():
    g = guard_for("rules: [{id: r, never: rm}]")
    g.record("Write", {"file_path": "a", "content": "x" * 10000}, "t", "y" * 10000)
    assert len(g.trace[-1]["arguments"]["content"]) < 2100
    assert len(g.trace[-1]["result"]) == 2000


class TestMemoryScopes:
    RULES = """
rules:
  - {id: tests-ever, before: [pytest, git_push], scope: project}
  - {id: tests-now, before: [ruff, git_push]}
"""

    def test_project_memory_survives_a_new_session_and_session_memory_does_not(self):
        rs = loads(self.RULES)

        def session(project_state):
            g = Guard(rs, Paths("/proj", "/proj"))
            g.restore({}, json.loads(json.dumps(project_state)))
            return g

        first = session({})
        assert run(first, "pytest", "ruff check", "git push") == ["none", "none", "none"]
        second = session(first.dump_project())       # new session, same project
        v = second.decide("Bash", {"command": "git push"})
        assert (v.action, v.rule) == ("deny", "tests-now")  # pytest is remembered, ruff is not
        assert [c["tool_name"] for c in second.project_trace] == ["pytest", "ruff", "git_push"]
        assert second.trace == []

    def test_scope_is_validated_and_defaults_from_settings(self):
        rs = loads("settings: {scope: project}\nrules: [{id: a, never: rm}, "
                   "{id: b, never: rm, scope: session}]")
        assert [(r.id, r.scope) for r in rs.rules] == [("a", "project"), ("b", "session")]
        with pytest.raises(RuleFileError, match="scope must be one of"):
            loads("rules: [{id: a, never: rm, scope: forever}]")
