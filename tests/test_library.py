"""The rule library: packaged rules switched on with `use:`, and `disable:`."""

import pytest

from agentltl_cc import cli
from agentltl_cc.guard import Guard, lint
from agentltl_cc.match import Paths
from agentltl_cc.rules import RuleFileError, library, load, loads

from .conftest import run

PACKS = library()


def guard_using(pack, cwd="/proj"):
    paths = Paths(cwd, cwd)
    g = Guard(loads(f"use: [{pack}]", paths), paths)
    g.restore({})
    return g


@pytest.mark.parametrize("name", sorted(PACKS))
def test_every_pack_compiles_without_warnings(name):
    pack = PACKS[name]
    assert pack.get("summary") and pack.get("tags") and pack.get("rules")
    ruleset = loads(f"use: [{name}]")
    assert ruleset.rules and all(r.why for r in ruleset.rules)
    assert lint(ruleset) == []


def test_rule_ids_are_unique_across_the_library():
    ids = [r["id"] for p in PACKS.values() for r in p["rules"]]
    assert len(ids) == len(set(ids))


AGENT = "Agent"
_BEHAVIOUR = [
    ("subagents-on-sonnet", [(AGENT, {"prompt": "x"}), (AGENT, {"prompt": "x", "model": "opus"}),
                             (AGENT, {"prompt": "x", "model": "sonnet"}),
                             (AGENT, {"prompt": "x", "model": "sonnet", "subagent_type": "fork"})],
     ["deny", "deny", "none", "deny"]),
    ("no-claude-coauthor", ['git commit -m x -m "Co-Authored-By: Claude <noreply@anthropic.com>"',
                            'git commit -m "fix bug"'], ["deny", "none"]),
    ("no-force-push", ["git push -f", "git push --force-with-lease", "git push"],
     ["deny", "none", "none"]),
    ("no-push-to-main", ["git push origin main", "git push origin HEAD:master",
                         "git push origin feature"], ["deny", "deny", "none"]),
    ("ask-before-discarding-work", ["git reset --hard", "git clean -fd", "git branch -D x",
                                    "git stash drop", "git checkout -- .", "git reset HEAD~1",
                                    "git checkout main"], ["ask"] * 5 + ["none"] * 2),
    ("protect-env-files", ["cat .env", "cat .env.example"], ["stop", "none"]),
    ("ask-before-installing", ["pip install x", "npm i x", "uv add x", "uv pip install x",
                               "python -m pip install x", "brew install x", "npm test",
                               "uv run pytest"], ["ask"] * 6 + ["none"] * 2),
    ("ask-before-recursive-delete", ["rm -rf build", "rm a.txt"], ["ask", "none"]),
    ("ask-before-infra-changes", ["terraform apply", "kubectl delete pod x", "terraform plan",
                                  "kubectl get pods"], ["ask", "ask", "none", "none"]),
    ("tests-before-push", ["git push", "uv run pytest", "git push",
                           ("Edit", {"file_path": "a.py"}), "git push", "npm test", "git push"],
     ["deny", "none", "none", "none", "deny", "none", "none"]),
]


@pytest.mark.parametrize("name, steps, expected", _BEHAVIOUR, ids=[b[0] for b in _BEHAVIOUR])
def test_pack_behaviour(name, steps, expected):
    assert run(guard_using(name), *steps) == expected


def test_read_before_overwrite(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    g = guard_using("read-before-overwrite", str(tmp_path))
    write = ("Write", {"file_path": "a.txt"})
    assert run(g, write, ("Read", {"file_path": "a.txt"}), write,
               ("Write", {"file_path": "new.txt"})) == ["deny", "none", "none", "none"]


class TestUse:
    def test_mode_override_and_replacement_by_id(self):
        rs = loads("use: [{no-force-push: {mode: warn}}, ask-before-recursive-delete]\n"
                   "rules: [{id: ask-before-recursive-delete, never: rm, mode: log}]")
        assert {r.id: (r.mode, r.source) for r in rs.rules} == {
            "no-force-push": ("warn", "library:no-force-push"),
            "ask-before-recursive-delete": ("log", "<rules>")}
        assert rs.used == ["no-force-push", "ask-before-recursive-delete"]

    def test_unknown_name_suggests_the_closest(self):
        with pytest.raises(RuleFileError, match="did you mean 'no-force-push'"):
            loads("use: [no-forcepush]")


class TestDisable:
    def test_a_project_can_switch_off_a_user_rule_and_the_built_in_one(self, tmp_path):
        user, project = tmp_path / "u.yaml", tmp_path / "p.yaml"
        user.write_text("rules: [{id: a, never: x}, {id: b, never: y}]")
        project.write_text("disable: [a, memory-first]\nuse: [no-force-push]\n"
                           "rules: [{id: c, never: z}]")
        assert [r.id for r in load([str(user), str(project)]).rules] == ["b", "no-force-push", "c"]
        assert "memory-first" in [r.id for r in load([str(user)]).rules]

    def test_one_rule_of_a_pack(self):
        rs = loads("use: [subagents-on-sonnet]\ndisable: [subagents-on-sonnet-no-fork]")
        assert [r.id for r in rs.rules] == ["subagents-on-sonnet"]


class TestCommands:
    @pytest.fixture
    def here(self, tmp_path, monkeypatch):
        (tmp_path / ".git").mkdir()
        monkeypatch.chdir(tmp_path)
        return tmp_path / "AGENTLTL.yaml"

    def test_use_unuse_disable_enable_keep_the_rest_of_the_file(self, here):
        here.write_text("# my rules\nrules:\n  - id: mine   # keep me\n    never: rm\n")
        assert cli.main(["use", "no-force-push", "tests-before-push"]) == 0
        assert cli.main(["use", "tests-before-push", "--mode", "warn"]) == 0
        assert cli.main(["disable", "memory-first"]) == 0
        assert cli.main(["unuse", "no-force-push"]) == 0
        text = here.read_text()
        assert "# my rules" in text and "- id: mine   # keep me" in text
        rs = loads(text)
        assert rs.used == ["tests-before-push"] and rs.disabled == ["memory-first"]
        assert rs.get("tests-before-push").mode == "warn"
        assert cli.main(["enable", "memory-first"]) == 0
        assert "disable" not in here.read_text()

    def test_use_creates_the_file_and_rejects_unknown_names(self, here, capsys):
        assert cli.main(["use", "no-forcepush"]) == 1
        assert "did you mean 'no-force-push'" in capsys.readouterr().err
        assert not here.exists()
        assert cli.main(["use", "no-force-push"]) == 0
        assert [r.id for r in loads(here.read_text()).rules] == ["no-force-push"]

    def test_library_lists_what_is_on(self, here, capsys):
        here.write_text("use: [no-force-push]\n")
        assert cli.main(["library"]) == 0
        line = next(ln for ln in capsys.readouterr().out.splitlines() if "no-force-push" in ln)
        assert line.split()[0] == "on"
