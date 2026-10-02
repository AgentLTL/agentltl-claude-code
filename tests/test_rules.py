import pytest

from agentltl_guard.guard import lint
from agentltl_guard.match import Paths, parse_target
from agentltl_guard.rules import RuleFileError, find_rule_file, load, loads

from .conftest import guard_for, run

P = Paths("/proj/src", "/proj")


class TestTargets:
    @pytest.mark.parametrize("spec,name,args,expected", [
        ("git_push", "git_push", {}, True),
        ("git_push", "git_commit", {}, False),
        (["Edit", "Write"], "Write", {}, True),
        ({"tool": "git_push", "with": {"force": True}}, "git_push", {"force": True}, True),
        ({"tool": "git_push", "with": {"force": True}}, "git_push", {"force": False}, False),
        ({"tool": "git_push", "with": {"force": False}}, "git_push", {}, True),
        ({"tool": "rm", "with": {"paths": "a"}}, "rm", {"paths": ["b", "a"]}, True),
        ({"tool": "npm", "with": {"argv": "install"}}, "npm", {"argv": ["install", "x"]}, True),
        ({"tool": "Edit", "where": {"file_path": "*.lock"}}, "Edit",
         {"file_path": "/proj/poetry.lock"}, True),
        ({"tool": "Edit", "where": {"file_path": "**/.env"}}, "Edit", {"file_path": ".env"}, True),
        ({"tool": "rm", "where": {"paths": "src/*.py"}}, "rm", {"paths": ["a.py"]}, True),
        ({"tool": "rm", "where": {"paths": "src/*.py"}}, "rm", {"paths": ["../a.py"]}, False),
        ({"tool": "cat", "where": {"*": "**/.env"}}, "cat", {"paths": ["x", "../.env"]}, True),
        ({"tool": "cat", "where": {"*": "**/.env"}}, "cat", {"paths": ["x"]}, False),
        ([{"tool": "rm"}, {"tool": "Edit", "where": {"file_path": "*.lock"}}], "rm", {}, True),
        ({"tool": "Edit", "with": {"a": 1}, "where": {"file_path": "*.py"}}, "Edit",
         {"a": 1, "file_path": "x.txt"}, False),
    ])
    def test_matches(self, spec, name, args, expected):
        assert parse_target(spec, "t").matches(name, args, P) is expected


def test_exists(tmp_path):
    (tmp_path / "m").mkdir()
    (tmp_path / "m" / "001.sql").write_text("x")
    paths = Paths(str(tmp_path), str(tmp_path))
    old = parse_target({"tool": "Write", "where": {"file_path": "m/*"}, "exists": True}, "t")
    new = parse_target({"tool": "Write", "where": {"file_path": "m/*"}, "exists": False}, "t")
    assert old.matches("Write", {"file_path": "m/001.sql"}, paths)
    assert not old.matches("Write", {"file_path": "m/002.sql"}, paths)
    assert new.matches("Write", {"file_path": "m/002.sql"}, paths)
    with pytest.raises(Exception, match="next to a 'where'"):
        parse_target({"tool": "Write", "exists": True}, "t")


class TestValidation:
    @pytest.mark.parametrize("text,fragment", [
        ("rules: [{id: x}]", "needs exactly one of"),
        ("rules: [{id: x, never: a, before: [a, b]}]", "has never, before"),
        ("rules: [{never: a}]", "missing 'id'"),
        ("rules: [{id: x, never: a, mode: loud}]", "mode must be one of"),
        ("rules: [{id: x, never: a}, {id: x, never: b}]", "duplicate id 'x'"),
        ("rules: [{id: x, never: a, colour: red}]", "unknown key(s) ['colour']"),
        ("rules: [{id: x, before: [a]}]", "expected [first, then]"),
        ("rules: [{id: x, before: [a, b], with: {f: 1}}]", "only apply to never"),
        ("rules: [{id: x, require: git_push}]", "require needs 'with' or 'where'"),
        ("rules: [{id: x, at_most: {call: a}}]", "expected {call: <target>, times: <n>}"),
        ("rules: [{id: x, ltl: 'F(called(\"a\"))'}]", "liveness property"),
        ("rules: [{id: x, ltl: 'before(\"a\", \"b\")'}]", "liveness property"),
        ("rules: [{id: x, ltl: 'before(('}]", "cannot parse formula"),
        ("rules: [{id: x, formula: {type: Nope}}]", "unknown formula type"),
        ("rules: [{id: x, formula: {type: Before, args: {c: 1}}}]", "has no argument(s) ['c']"),
        ("settings: {mode: nope}\nrules: []", "mode must be one of"),
        ("settings: {unparseable: maybe}\nrules: []", "unparseable"),
        ("rules: [{id: x, never: a}]\nextra: 1", "unknown top-level key(s) ['extra']"),
        ("rules: [\n", "invalid YAML"),
    ])
    def test_problems_are_reported(self, text, fragment):
        with pytest.raises(RuleFileError) as exc:
            loads(text, source="f.yaml")
        assert fragment in str(exc.value)

    def test_problems_carry_the_line_of_the_rule(self):
        with pytest.raises(RuleFileError) as exc:
            loads("rules:\n  - id: ok\n    never: a\n  - id: bad\n", source="f.yaml")
        assert exc.value.problems == [
            "f.yaml:4: rule 'bad' needs exactly one of never, before, require, at_most, ltl, formula"]

    def test_structured_formula(self):
        rs = loads("rules: [{id: x, formula: {type: Not, args: {operand: "
                   "{type: Called, args: {tool: rm}}}}}]")
        assert str(rs.rules[0].formula) == '¬(called("rm"))'

    def test_lint_flags_names_nothing_produces(self):
        rs = loads("""
rules:
  - {id: a, never: git_push, with: {repository: origin}}
  - {id: b, before: [make_test, git_push]}
  - {id: c, never: frobnicate, with: {level: 3}}
  - {id: d, never: [Edit, git_push], with: {force: true}}
  - {id: e, never: {tool: npm, with: {argv: install}}}
""")
        warnings = lint(rs)
        assert [w.split(":")[0] for w in warnings] == ["a", "b", "c"]
        assert "'remote'" in warnings[0] and "make_test" in warnings[1]


class TestFiles:
    def test_found_upwards_but_not_past_the_git_root(self, tmp_path):
        (tmp_path / "AGENTLTL.yaml").write_text("rules: []")
        repo = tmp_path / "repo"
        (repo / ".git").mkdir(parents=True)
        (repo / "a" / "b").mkdir(parents=True)
        assert find_rule_file(str(repo / "a" / "b")) is None
        (repo / "AGENTLTL.yaml").write_text("rules: []")
        assert find_rule_file(str(repo / "a" / "b")) == str(repo / "AGENTLTL.yaml")

    def test_project_rules_override_user_rules_by_id(self, tmp_path):
        user, project = tmp_path / "u.yaml", tmp_path / "p.yaml"
        user.write_text("settings: {mode: warn}\nrules: [{id: a, never: x}, {id: b, never: y}]")
        project.write_text("rules: [{id: b, never: z, mode: stop}]")
        rs = load([str(user), str(project)])
        assert [(r.id, r.tools, r.mode) for r in rs.rules] == [
            ("a", ("x",), "warn"), ("b", ("z",), "stop")]
        assert rs.settings.mode == "warn"


class TestKinds:
    def test_never(self):
        g = guard_for("rules: [{id: r, never: git_push, with: {force: true}}]")
        assert run(g, "git push", "git push -f origin main", "git push --force") == [
            "none", "deny", "deny"]

    def test_before_and_chains(self):
        g = guard_for("rules: [{id: r, before: [pytest, git_push]}]")
        assert run(g, "git push", "pytest && git push", "git push") == ["deny", "none", "none"]

    def test_before_since(self):
        g = guard_for("rules: [{id: r, before: {first: pytest, then: git_push, since: [Edit]}}]")
        edit = ("Edit", {"file_path": "/proj/a.py"})
        assert run(g, "pytest", edit, "git push", "pytest", "git push") == [
            "none", "none", "deny", "none", "none"]

    def test_require(self):
        g = guard_for("rules: [{id: r, require: {tool: rm, where: {paths: [build, 'build/**']}}}]")
        assert run(g, "rm -rf build", "rm build/x.o", "rm src/a.py", "ls") == [
            "none", "none", "deny", "none"]

    def test_at_most(self):
        g = guard_for("rules: [{id: r, at_most: {call: git_commit, times: 2}}]")
        # the chain would make three, so none of it runs
        assert run(g, "git commit -m a", "git commit -m b && git commit -m c",
                   "git commit -m d", "git commit -m e") == ["none", "deny", "none", "deny"]

    def test_ltl(self):
        g = guard_for("""rules: [{id: r, ltl: 'G(called("git_rebase") -> before("git_fetch", "git_rebase"))'}]""")
        assert run(g, "git rebase main", "git fetch && git rebase main") == ["deny", "none"]

    def test_a_broken_rule_does_not_block_unrelated_calls(self):
        g = guard_for("rules: [{id: r, never: rm, mode: warn}]")
        assert run(g, "rm a", "rm a", "ls", "rm b") == ["deny", "none", "none", "deny"]
