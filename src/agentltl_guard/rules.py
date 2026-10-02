"""
agentltl_guard/rules.py – AGENTLTL.yaml → AgentLTL constraints.

    settings:
      mode: block               # default escalation for every rule
      retries: 3                # attempts allowed by mode: retry before asking you
      unparseable: {interactive: ask, auto: note}
      announce: true            # list the rules to Claude at session start and after compaction
    rules:
      - id: tests-before-push
        before: {first: pytest, then: git_push, since: [Edit, Write]}
        why: CI is slow; run the tests locally first.
        fix: Run pytest, then push.
        mode: warn
    tools:                      # cli-to-tools specs for your own commands
      deploy: {options: [{flags: [--prod], type: bool}]}

Rule kinds (exactly one per rule; targets are described in :mod:`agentltl_guard.match`):

    never: T                  T is never called
    before: [A, B]            B only once A has been called; dict form adds ``since: S``
                              (an A must come after the last S)
    require: T                when one of T's tools is called, its arguments match T
    at_most: {call: T, times: n}
    ltl: '...'                raw AgentLTL formula (``agentltl.parse`` syntax)
    formula: {type, args}     structured AgentLTL formula

``never``, ``require`` and ``at_most`` also take ``with:`` / ``where:`` at rule level.

Rules from ``~/.claude/AGENTLTL.yaml`` apply everywhere; the project file adds to them and
replaces a user rule with the same id.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import yaml

from .match import Paths, RuleError, parse_target

FILE_NAME = "AGENTLTL.yaml"

# escalation mode -> AgentLTL ConstraintSeverity name
MODES: Dict[str, str] = {
    "block": "PERSISTENT_BLOCK",  # denied every time; Claude cannot override
    "warn": "BLOCK_AND_WARN",     # denied once; Claude may insist by repeating the exact call
    "retry": "SOFT_BLOCK",        # denied up to `retries` times, then you are asked
    "ask": "PERSISTENT_BLOCK",    # you are asked every time
    "stop": "HARD_STOP",          # denied and Claude stops working
    "log": "TOLERATE",            # allowed; Claude is told it broke the rule
}
MODE_HELP: Dict[str, str] = {
    "block": "denied every time; only you can let it through (edit the rule, or run it yourself)",
    "warn": "denied once; Claude may override by repeating the exact same call",
    "retry": "denied, and after the allowed retries you are asked to approve",
    "ask": "you are asked to approve every time",
    "stop": "denied and Claude stops working until you reply",
    "log": "allowed, but Claude is told the rule was broken",
}
# The first violated rule decides a call, so stronger modes are checked first. That also keeps
# a `warn` override (repeat the exact call) from slipping past a stronger rule the same call
# breaks: had one been broken, it would have decided the call instead.
STRENGTH = ("stop", "block", "ask", "retry", "warn", "log")
UNPARSEABLE = ("ask", "note", "allow", "deny")
KINDS = ("never", "before", "require", "at_most", "ltl", "formula")
_RULE_KEYS = {"id", "why", "fix", "mode", "with", "where", *KINDS}


@dataclass
class Settings:
    mode: str = "block"
    retries: int = 3
    unparseable_interactive: str = "ask"
    unparseable_auto: str = "note"
    announce: bool = True


@dataclass
class Rule:
    id: str
    kind: str
    why: str
    fix: str
    mode: str
    formula: Any
    tools: Tuple[str, ...] = ()
    source: str = ""
    targets: Tuple[Any, ...] = ()

    @property
    def summary(self) -> str:
        """One line saying what the rule checks."""
        return getattr(self.formula, "description", None) or str(self.formula)

    def constraint(self) -> Any:
        from agentltl import Constraint
        return Constraint(self.id, self.formula, description=self.why, repair=self.fix,
                          enforcement="at_call")


@dataclass
class RuleSet:
    rules: List[Rule] = field(default_factory=list)
    settings: Settings = field(default_factory=Settings)
    tool_specs: Dict[str, Any] = field(default_factory=dict)
    files: List[str] = field(default_factory=list)
    explicit_settings: bool = False

    def get(self, rule_id: str) -> Optional[Rule]:
        return next((r for r in self.rules if r.id == rule_id), None)

    def constraints(self) -> List[Any]:
        """Constraints, strongest mode first (see STRENGTH)."""
        ordered = sorted(self.rules, key=lambda r: STRENGTH.index(r.mode))
        return [r.constraint() for r in ordered]

    def severities(self) -> Dict[str, Any]:
        from agentltl import ConstraintSeverity
        return {r.id: ConstraintSeverity[MODES[r.mode]] for r in self.rules}


class RuleFileError(Exception):
    """One or more problems in a rule file; ``problems`` lists them with locations."""

    def __init__(self, problems: List[str]) -> None:
        super().__init__("\n".join(problems))
        self.problems = problems


# ── locating and loading ──────────────────────────────────────────────────────

def find_rule_file(start: str, stop: Optional[str] = None) -> Optional[str]:
    """The nearest AGENTLTL.yaml from *start* upwards, not above *stop* or the git root."""
    here = os.path.abspath(start or os.getcwd())
    stop = os.path.abspath(stop) if stop else None
    while True:
        candidate = os.path.join(here, FILE_NAME)
        if os.path.isfile(candidate):
            return candidate
        if here == stop or os.path.exists(os.path.join(here, ".git")):
            return None
        parent = os.path.dirname(here)
        if parent == here:
            return None
        here = parent


def user_rule_file() -> Optional[str]:
    path = os.path.join(os.path.expanduser("~"), ".claude", FILE_NAME)
    return path if os.path.isfile(path) else None


def rule_files(cwd: str, project_dir: Optional[str] = None) -> List[str]:
    """User-level then project-level rule files that apply in *cwd*."""
    files = [f for f in (user_rule_file(), find_rule_file(cwd, project_dir)) if f]
    return list(dict.fromkeys(files))


def load(files: List[str], paths: Optional[Paths] = None) -> RuleSet:
    """Compile the rule files, later files overriding earlier ones.

    Raises:
        RuleFileError: With every problem found, each prefixed by ``file:line``.
    """
    merged = RuleSet(files=list(files))
    problems: List[str] = []
    for path in files:
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            part = loads(text, paths, source=path)
        except RuleFileError as exc:
            problems += exc.problems
            continue
        except OSError as exc:
            problems.append(f"{path}: {exc}")
            continue
        by_id = {r.id: i for i, r in enumerate(merged.rules)}
        for rule in part.rules:
            if rule.id in by_id:
                merged.rules[by_id[rule.id]] = rule
            else:
                merged.rules.append(rule)
        merged.tool_specs.update(part.tool_specs)
        if part.explicit_settings:
            merged.settings = part.settings
    if problems:
        raise RuleFileError(problems)
    return merged


def loads(text: str, paths: Optional[Paths] = None, source: str = "<rules>") -> RuleSet:
    """Compile one rule file's text."""
    try:
        data = yaml.safe_load(text) or {}
        lines = _rule_lines(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f"{source}:{mark.line + 1}" if mark else source
        raise RuleFileError([f"{where}: invalid YAML: {getattr(exc, 'problem', exc)}"]) from None
    if not isinstance(data, dict):
        raise RuleFileError([f"{source}: expected a mapping with 'rules' (and optionally "
                             "'settings', 'tools')"])
    problems: List[str] = []
    unknown = set(data) - {"version", "settings", "rules", "tools"}
    if unknown:
        problems.append(f"{source}: unknown top-level key(s) {sorted(unknown)}")
    settings = Settings()
    try:
        settings = _settings(data.get("settings"))
    except RuleError as exc:
        problems.append(f"{source}: settings: {exc}")

    tools = data.get("tools") or {}
    if not isinstance(tools, dict):
        problems.append(f"{source}: 'tools' must map command names to cli-to-tools specs")
        tools = {}
    else:
        problems += _check_tools(tools, source)

    raw_rules = data.get("rules") or []
    if not isinstance(raw_rules, list):
        raise RuleFileError(problems + [f"{source}: 'rules' must be a list"])
    rules: List[Rule] = []
    seen: Dict[str, str] = {}
    paths = paths or Paths()
    for i, raw in enumerate(raw_rules):
        where = f"{source}:{lines[i]}" if i < len(lines) else f"{source}: rules[{i}]"
        try:
            rule = compile_rule(raw, settings, paths, where)
        except RuleError as exc:
            problems.append(f"{where}: {exc}")
            continue
        if rule.id in seen:
            problems.append(f"{where}: duplicate id '{rule.id}' (first at {seen[rule.id]})")
            continue
        seen[rule.id] = where
        rules.append(rule)
    if problems:
        raise RuleFileError(problems)
    return RuleSet(rules, settings, tools, [source], "settings" in data)


def _rule_lines(text: str) -> List[int]:
    node = yaml.compose(text)
    if not isinstance(node, yaml.MappingNode):
        return []
    for key, value in node.value:
        if key.value == "rules" and isinstance(value, yaml.SequenceNode):
            return [item.start_mark.line + 1 for item in value.value]
    return []


def _settings(raw: Any) -> Settings:
    if raw is None:
        return Settings()
    if not isinstance(raw, dict):
        raise RuleError("expected a mapping")
    unknown = set(raw) - {"mode", "retries", "unparseable", "announce"}
    if unknown:
        raise RuleError(f"unknown key(s) {sorted(unknown)}")
    s = Settings()
    if "mode" in raw:
        s.mode = _mode(raw["mode"])
    if "retries" in raw:
        if not isinstance(raw["retries"], int) or raw["retries"] < 1:
            raise RuleError("retries must be a positive integer")
        s.retries = raw["retries"]
    if "announce" in raw:
        if not isinstance(raw["announce"], bool):
            raise RuleError("announce must be true or false")
        s.announce = raw["announce"]
    unp = raw.get("unparseable")
    if isinstance(unp, str):
        unp = {"interactive": unp, "auto": unp}
    if unp is not None:
        if not isinstance(unp, dict) or set(unp) - {"interactive", "auto"}:
            raise RuleError("unparseable must be one of ask/note/allow/deny, or a mapping with "
                            "'interactive' and/or 'auto'")
        for key, value in unp.items():
            if value not in UNPARSEABLE:
                raise RuleError(f"unparseable.{key} must be one of {', '.join(UNPARSEABLE)}")
        s.unparseable_interactive = unp.get("interactive", s.unparseable_interactive)
        s.unparseable_auto = unp.get("auto", s.unparseable_auto)
    return s


def _mode(value: Any) -> str:
    if value not in MODES:
        raise RuleError(f"mode must be one of {', '.join(MODES)}, got {value!r}")
    return value


def _check_tools(tools: Dict[str, Any], source: str) -> List[str]:
    try:
        from cli_to_tools import SpecRegistry
    except ImportError:
        return []
    try:
        SpecRegistry(packs=[]).load_dict(tools)
    except Exception as exc:  # the spec loader raises whatever argparse or YAML shapes give
        return [f"{source}: tools: {exc}"]
    return []


# ── compiling one rule ────────────────────────────────────────────────────────

def compile_rule(raw: Any, settings: Settings, paths: Paths, where: str = "rule") -> Rule:
    if not isinstance(raw, dict):
        raise RuleError("a rule must be a mapping with an id and one rule kind")
    unknown = set(raw) - _RULE_KEYS
    if unknown:
        raise RuleError(f"unknown key(s) {sorted(unknown)}; rule kinds are {', '.join(KINDS)}")
    rule_id = raw.get("id")
    if not isinstance(rule_id, str) or not rule_id.strip():
        raise RuleError("missing 'id'")
    kinds = [k for k in KINDS if k in raw]
    if len(kinds) != 1:
        raise RuleError(f"rule '{rule_id}' needs exactly one of {', '.join(KINDS)}"
                        + (f", has {', '.join(kinds)}" if kinds else ""))
    kind = kinds[0]
    if ("with" in raw or "where" in raw) and kind not in ("never", "require", "at_most"):
        raise RuleError(f"rule '{rule_id}': 'with'/'where' at rule level only apply to "
                        "never, require and at_most; put them on a target instead")
    mode = _mode(raw.get("mode", settings.mode))
    why = str(raw.get("why") or "").strip()
    fix = str(raw.get("fix") or "").strip()
    formula, tools, *targets = _BUILDERS[kind](raw, paths, f"{rule_id}.{kind}")
    if kind in ("ltl", "formula"):
        _require_runtime_safe(formula, rule_id)
    return Rule(rule_id, kind, why, fix, mode, formula, tools, where, tuple(targets))


def _predicate(fn: Callable[[List[Any]], Optional[str]], label: str) -> Any:
    """A Predicate that judges only the newest call of the trace: the one being checked.

    Judging the newest call alone keeps a rule from blocking unrelated calls once it has been
    broken (by an override, or by a call made before the rule existed).
    """
    from agentltl import Predicate

    def run(trace: Any, position: int = 0, metrics: Any = None) -> Any:
        calls = list(trace.calls)
        if not calls:
            return True
        problem = fn(calls)
        return True if problem is None else {"passed": False, "note": problem}

    return Predicate(run, label, runtime_safe=True)


def _never(raw: Dict[str, Any], paths: Paths, where: str) -> Tuple[Any, ...]:
    target = parse_target(raw["never"], where, with_=raw.get("with"), where_=raw.get("where"))

    def check(calls: List[Any]) -> Optional[str]:
        c = calls[-1]
        if target.matches(c.name, c.args, paths):
            return f"{c.name} is not allowed: it matches {target.describe()}."
        return None

    return _predicate(check, f"never {target.describe()}"), target.tools, target


def _before(raw: Dict[str, Any], paths: Paths, where: str) -> Tuple[Any, ...]:
    spec = raw["before"]
    since = None
    if isinstance(spec, list) and len(spec) == 2:
        first, then = spec
    elif isinstance(spec, dict) and {"first", "then"} <= set(spec) and not (
            set(spec) - {"first", "then", "since"}):
        first, then = spec["first"], spec["then"]
        if spec.get("since") is not None:
            since = parse_target(spec["since"], f"{where}.since")
    else:
        raise RuleError(f"{where}: expected [first, then] or {{first, then, since}}")
    a = parse_target(first, f"{where}.first")
    b = parse_target(then, f"{where}.then")

    def check(calls: List[Any]) -> Optional[str]:
        c = calls[-1]
        if not b.matches(c.name, c.args, paths):
            return None
        start = -1
        if since is not None:
            start = max((i for i, p in enumerate(calls[:-1])
                         if since.matches(p.name, p.args, paths)), default=-1)
        if any(a.matches(p.name, p.args, paths) for p in calls[start + 1:-1]):
            return None
        tail = ""
        if since is not None and start >= 0:
            tail = f" since the last {since.describe()} (call #{start + 1})"
        return f"{c.name} needs {a.describe()} to have run first{tail}."

    label = f"{a.describe()} before {b.describe()}" + (f" since {since.describe()}" if since else "")
    return (_predicate(check, label), a.tools + b.tools, a, b) + ((since,) if since else ())


def _require(raw: Dict[str, Any], paths: Paths, where: str) -> Tuple[Any, ...]:
    target = parse_target(raw["require"], where, with_=raw.get("with"), where_=raw.get("where"))
    if not target.with_ and not target.where:
        raise RuleError(f"{where}: require needs 'with' or 'where' (what the arguments must be)")

    def check(calls: List[Any]) -> Optional[str]:
        c = calls[-1]
        if c.name in target.tools and not target.matches(c.name, c.args, paths):
            return f"{c.name} must be called as {target.describe()}."
        return None

    return _predicate(check, f"require {target.describe()}"), target.tools, target


def _at_most(raw: Dict[str, Any], paths: Paths, where: str) -> Tuple[Any, ...]:
    spec = raw["at_most"]
    if not isinstance(spec, dict) or set(spec) != {"call", "times"}:
        raise RuleError(f"{where}: expected {{call: <target>, times: <n>}}")
    times = spec["times"]
    if not isinstance(times, int) or times < 0:
        raise RuleError(f"{where}.times: expected a non-negative integer")
    target = parse_target(spec["call"], f"{where}.call", with_=raw.get("with"),
                          where_=raw.get("where"))

    def check(calls: List[Any]) -> Optional[str]:
        c = calls[-1]
        if not target.matches(c.name, c.args, paths):
            return None
        n = sum(1 for p in calls[:-1] if target.matches(p.name, p.args, paths))
        if n >= times:
            return f"{target.describe()} may run at most {times} time(s); it already ran {n}."
        return None

    return _predicate(check, f"at most {times} x {target.describe()}"), target.tools, target


def _ltl(raw: Dict[str, Any], paths: Paths, where: str) -> Tuple[Any, ...]:
    from agentltl import parse
    text = raw["ltl"]
    if not isinstance(text, str):
        raise RuleError(f"{where}: expected a formula string")
    try:
        formula = parse(text)
    except Exception as exc:
        raise RuleError(f"{where}: cannot parse formula: {exc}") from None
    return formula, tuple(sorted(_tools_in(formula)))


def _formula(raw: Dict[str, Any], paths: Paths, where: str) -> Tuple[Any, ...]:
    formula = build_formula(raw["formula"], where)
    return formula, tuple(sorted(_tools_in(formula)))


_BUILDERS = {"never": _never, "before": _before, "require": _require, "at_most": _at_most,
             "ltl": _ltl, "formula": _formula}


def build_formula(spec: Any, where: str = "formula") -> Any:
    """``{type: Before, args: {a: x, b: y}}`` → ``Before("x", "y")``, recursively."""
    import dataclasses

    import agentltl

    if isinstance(spec, list):
        return [build_formula(s, where) for s in spec]
    if not (isinstance(spec, dict) and "type" in spec):
        return spec
    cls = getattr(agentltl, str(spec["type"]), None)
    if not (isinstance(cls, type) and issubclass(cls, agentltl.Formula)) or cls is agentltl.Predicate:
        raise RuleError(f"{where}: unknown formula type {spec['type']!r}")
    args = spec.get("args") or {}
    if not isinstance(args, dict):
        raise RuleError(f"{where}: 'args' must be a mapping")
    fields = {f.name for f in dataclasses.fields(cls)}
    unknown = set(args) - fields
    if unknown:
        raise RuleError(f"{where}: {cls.__name__} has no argument(s) {sorted(unknown)}; "
                        f"expected {sorted(fields)}")
    try:
        return cls(**{k: build_formula(v, f"{where}.{k}") for k, v in args.items()})
    except TypeError as exc:
        raise RuleError(f"{where}: {exc}") from None


def _tools_in(formula: Any) -> set:
    import dataclasses
    out: set = set()
    if isinstance(formula, (list, tuple)):
        for f in formula:
            out |= _tools_in(f)
        return out
    if not dataclasses.is_dataclass(formula):
        return out
    for f in dataclasses.fields(formula):
        value = getattr(formula, f.name)
        if f.name in ("tool", "a", "b", "tool_a", "tool_b", "target", "tools") and value:
            out |= {value} if isinstance(value, str) else {v for v in value if isinstance(v, str)}
        else:
            out |= _tools_in(value)
    return out


def _require_runtime_safe(formula: Any, rule_id: str) -> None:
    """Reject what AgentLTL's classifier calls unsafe to enforce: liveness properties, which
    a check made call by call cannot judge."""
    from agentltl.runtime_safety import RuntimeSafety, classify_runtime_safety
    if classify_runtime_safety(formula).safety == RuntimeSafety.UNSAFE:
        raise RuleError(
            f"rule '{rule_id}': this is a liveness property (AgentLTL classifies it as unsafe "
            "to enforce): it can only be judged when the session ends, and the guard checks "
            "each call as it is made. Use a rule kind such as `before: [pytest, git_push]`, or "
            "guard the formula with G(called(...) -> ...), e.g. "
            "`G(called(\"git_push\") -> before(\"pytest\", \"git_push\"))`.")
