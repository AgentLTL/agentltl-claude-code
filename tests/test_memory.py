"""`agentltl memory`: splitting Claude's memory into statements that could become rules."""

import json
import os

import pytest

from agentltl_cc import cli, memory
from agentltl_cc.guard import translator_for
from agentltl_cc.match import Paths
from agentltl_cc.rules import RuleFileError, load, loads

CLAUDE_MD = """\
# Project notes

## Cloning

```bash
git submodule update --init          # NEVER --recursive
```

## Environment

Do **not** pip-install into a host env; run everything in `docker compose run --rm harness`.

- Before changing generation, read `docs/GENERATION_METHOD.md`.
- The repo is bind-mounted at /work.

| file | why |
|---|---|
| `docs/A.md` | the reference |

Prefer vLLM with parallel workers over TransformersModel for evaluations.
"""

AUTO_MEMORY = """\
---
name: no-self-attribution-in-commits
description: "Never add Claude as a commit author or co-author."
metadata:
  type: feedback
---

Never add a `Co-Authored-By` trailer. **How to apply:** no trailer at all.
"""


@pytest.fixture
def home(tmp_path):
    root, home = tmp_path / "proj", tmp_path / "home"
    (root / ".git").mkdir(parents=True)
    (root / "CLAUDE.md").write_text(CLAUDE_MD)
    (root / "sub").mkdir()
    (root / "sub" / "CLAUDE.md").write_text("Never run `make deploy` from here.\n")
    (root / "node_modules" / "x").mkdir(parents=True)
    (root / "node_modules" / "x" / "CLAUDE.md").write_text("Never touch this dependency.\n")
    mem = home / ".claude" / "projects" / memory.project_slug(str(root)) / "memory"
    mem.mkdir(parents=True)
    (mem / "no-self-attribution.md").write_text(AUTO_MEMORY)
    (mem / "MEMORY.md").write_text("- [no attribution](no-self-attribution.md)\n")
    (home / ".claude" / "CLAUDE.md").write_text("Always answer in English.\n")
    return root, home


def _texts(statements):
    return [s.text for s in statements]


class TestSplit:
    def test_bullets_paragraphs_rows_and_code(self):
        st = memory.split(CLAUDE_MD, "CLAUDE.md")
        texts = _texts(st)
        assert texts[0] == "git submodule update --init          # NEVER --recursive"
        assert texts[1].startswith("Do **not** pip-install")
        assert "Before changing generation, read `docs/GENERATION_METHOD.md`." in texts
        assert "`docs/A.md` | the reference" in texts            # the header row is skipped
        assert not any(t.startswith("file | why") for t in texts)
        assert st[0].section == "Project notes > Cloning" and st[0].line == 5

    def test_code_belongs_to_the_statement_before_it_in_the_same_section(self):
        st = memory.split("## Run\n\nAlways use the container:\n\n```\ndocker compose up\n```\n")
        assert len(st) == 1 and st[0].code == "docker compose up"

    def test_an_auto_memory_file_is_one_statement(self):
        (st,) = memory.split(AUTO_MEMORY, "m.md", "auto-memory")
        assert st.text == "Never add Claude as a commit author or co-author."
        assert "Co-Authored-By" in st.context and st.section == "feedback"

    def test_ids_survive_edits_elsewhere(self):
        before = {s.text: s.id for s in memory.split(CLAUDE_MD)}
        after = {s.text: s.id for s in memory.split("# New intro\n\nSome text here now.\n\n"
                                                    + CLAUDE_MD)}
        assert all(after[t] == i for t, i in before.items())
        assert memory.statement_id("Do **not** push") == memory.statement_id("do not push")


class TestSources:
    def test_what_applies_in_a_project(self, home):
        root, h = home
        found = {(os.path.relpath(s.path, h if "home" in s.path else root), s.kind, s.target)
                 for s in memory.sources(str(root), home=str(h))}
        slug = memory.project_slug(str(root))
        assert found == {
            ("CLAUDE.md", "claude-md", "project"),
            ("sub/CLAUDE.md", "claude-md", "project"),
            (f".claude/projects/{slug}/memory/no-self-attribution.md", "auto-memory", "project"),
            (".claude/CLAUDE.md", "claude-md", "user"),
        }

    def test_user_only(self, home):
        root, h = home
        assert [s.kind for s in memory.sources(str(root), user_only=True, home=str(h))] == [
            "claude-md"]

    def test_slug_matches_claude_code(self):
        assert memory.project_slug("/home/me/github/AgentLTL-BFCL") == \
            "-home-me-github-AgentLTL-BFCL"


class TestScan:
    def _scan(self, home, rules="rules: []\n"):
        root, h = home
        (root / "AGENTLTL.yaml").write_text(rules)
        paths = Paths(str(root), str(root))
        ruleset = load([str(root / "AGENTLTL.yaml")], paths)
        return memory.scan(str(root), ruleset, translator_for(ruleset), home=str(h))

    def test_candidates_and_hints(self, home):
        out = self._scan(home)
        by_text = {s["text"]: s for s in out["statements"]}
        sub = by_text["git submodule update --init          # NEVER --recursive"]
        assert sub["candidate"] and sub["emphasis"] and sub["file"] == "CLAUDE.md"
        (cmd,) = sub["commands"]
        assert cmd["calls"][0]["name"] == "git_submodule_update"
        assert cmd["calls"][0]["args"]["init"] is True and cmd["needs_spec"] == []
        assert by_text["Never add Claude as a commit author or co-author."]["candidate"]
        assert by_text["Never run `make deploy` from here."]["file"] == "sub/CLAUDE.md"
        assert not by_text["The repo is bind-mounted at /work."]["candidate"]
        assert by_text["Always answer in English."]["target"] == "user"
        assert by_text["Always answer in English."]["file"] == "~/.claude/CLAUDE.md"

    def test_a_command_without_a_spec_is_flagged(self):
        st = memory.Statement("x", "f", 1, "Never run `git filter-branch --msg-filter cat`.")
        (cmd,) = memory.commands_in(st, translator_for(loads("rules: []")))
        assert cmd["needs_spec"] == ["git_filter_branch"]

    def test_names_and_scripts_are_not_commands(self):
        st = memory.Statement("x", "f", 1, "Never edit `hub.py push` or `frobnicate 1.x` or `refsol`.")
        assert memory.commands_in(st, translator_for(loads("rules: []"))) == []

    def test_covered_and_declined(self, home, monkeypatch, tmp_path):
        monkeypatch.setenv("AGENTLTL_CC_STATE", str(tmp_path / "state" / "sessions"))
        root, _ = home
        sid = memory.statement_id("git submodule update --init          # NEVER --recursive")
        mem_id = memory.statement_id("Never add Claude as a commit author or co-author.")
        memory.decline(str(root), ["deadbeef00", memory.statement_id("Always answer in English.")])
        out = self._scan(home, "use: [{no-claude-coauthor: {from: %s}}]\n"
                               "rules:\n  - id: no-recursive-submodules\n"
                               "    never: {tool: git_submodule_update, with: {recursive: true}}\n"
                               "    from: {file: CLAUDE.md, line: 5, id: %s}\n" % (mem_id, sid))
        status = {s["id"]: (s["status"], s["rules"]) for s in out["statements"]}
        assert status[sid] == ("covered", ["no-recursive-submodules"])
        assert status[mem_id] == ("covered", ["no-claude-coauthor"])
        assert status[memory.statement_id("Always answer in English.")][0] == "declined"
        assert out["counts"]["covered"] == 2 and out["counts"]["declined"] == 1
        memory.forget(str(root), ["deadbeef00"])
        assert memory.declined_ids(str(root)) == [memory.statement_id("Always answer in English.")]


class TestFrom:
    def test_from_is_recorded_and_changes_nothing_else(self):
        rs = loads("rules:\n  - id: a\n    never: rm\n    from: {file: CLAUDE.md, line: 3, id: ab}\n"
                   "  - id: b\n    never: rm\n    from: [cd, {id: ef}]\n")
        assert rs.rules[0].origins == ({"file": "CLAUDE.md", "line": 3, "id": "ab"},)
        assert rs.rules[1].origins == ({"id": "cd"}, {"id": "ef"})
        assert rs.rules[0].summary == rs.rules[1].summary

    def test_bad_from(self):
        with pytest.raises(RuleFileError, match="'from' takes"):
            loads("rules: [{id: a, never: rm, from: {path: x}}]")

    def test_validate_shows_it(self, tmp_path, monkeypatch, capsys):
        (tmp_path / ".git").mkdir()
        (tmp_path / "AGENTLTL.yaml").write_text(
            "rules: [{id: a, never: rm, from: {file: CLAUDE.md, line: 3, id: ab}}]\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        assert cli.main(["validate"]) == 0
        assert "from CLAUDE.md:3" in capsys.readouterr().out

    def test_use_from(self, tmp_path, monkeypatch):
        (tmp_path / ".git").mkdir()
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        assert cli.main(["use", "no-force-push", "--from", "ab12", "--mode", "warn"]) == 0
        rs = load([str(tmp_path / "AGENTLTL.yaml")])
        assert [(r.id, r.mode, r.origins) for r in rs.rules if r.source != "built-in"] == [
            ("no-force-push", "warn", ({"id": "ab12"},))]


def test_cli_scan_json(home, monkeypatch, capsys):
    root, h = home
    monkeypatch.chdir(root)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.setenv("HOME", str(h))
    assert cli.main(["memory", "scan", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["statements"] and all(s["candidate"] for s in out["statements"])
    assert out["counts"]["statements"] > len(out["statements"])
