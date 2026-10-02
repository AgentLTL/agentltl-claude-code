"""
agentltl_cc/match.py – which calls a rule is talking about.

A target names one or more tools and optionally narrows them by argument:

    git_push                                  # any git push
    [Edit, Write]                             # either tool
    {tool: git_push, with: {force: true}}     # exact argument values
    {tool: [Edit, Write], where: {file_path: "**/.env"}}   # glob patterns

``with`` compares values for equality; when the call's value is a list, an expected scalar
must be one of its elements (``rm a b`` matches ``with: {paths: a}``). ``where`` matches
string values against glob patterns (``*`` also crosses ``/``); a path is tried as written,
resolved against the working directory, relative to the project root, and, for a pattern
without ``/``, by its basename. The key ``"*"`` stands for any argument:

    {tool: [Edit, Write, rm, mv, cat], where: {"*": "**/.env"}}

``exists: true`` (or ``false``) additionally requires a value that ``where`` matched to be a
path that exists (or does not) at the moment of the check, so "never modify an existing
migration" does not also catch creating a new one:

    {tool: Write, where: {file_path: "migrations/*"}, exists: true}

A list of targets matches when any of them does:

    [{tool: Write, where: {file_path: "*.lock"}}, {tool: rm, where: {paths: "*.lock"}}]
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple


class RuleError(ValueError):
    """A rule file entry that cannot be compiled."""


@dataclass(frozen=True)
class Paths:
    """Where relative paths in arguments are resolved from."""

    cwd: str = ""
    root: str = ""


@dataclass
class Target:
    tools: Tuple[str, ...]
    with_: Dict[str, Any] = field(default_factory=dict)
    where: Dict[str, Tuple[str, ...]] = field(default_factory=dict)
    exists: Optional[bool] = None

    def matches(self, name: str, args: Optional[Dict[str, Any]], paths: Paths) -> bool:
        if name not in self.tools:
            return False
        args = args or {}
        for key, expected in self.with_.items():
            if not _equal(expected, args.get(key)):
                return False
        hits: List[str] = []
        for key, patterns in self.where.items():
            values = _strings(list(args.values()) if key == "*" else args.get(key))
            found = [v for v in values if any(_glob(v, p, paths) for p in patterns)]
            if not found:
                return False
            hits += found
        if self.exists is not None:
            return any(os.path.exists(_absolute(v, paths)) == self.exists for v in hits)
        return True

    def describe(self) -> str:
        tools = " or ".join(self.tools)
        parts = [f"{k}={v!r}" for k, v in self.with_.items()]
        parts += [f"{k} matching {' or '.join(v)}" for k, v in self.where.items()]
        if self.exists is not None:
            parts.append("existing path" if self.exists else "new path")
        return f"{tools} ({', '.join(parts)})" if parts else tools


@dataclass
class AnyTarget:
    """Several targets; a call matches when it matches any of them."""

    targets: Tuple[Target, ...]

    @property
    def tools(self) -> Tuple[str, ...]:
        return tuple(dict.fromkeys(t for target in self.targets for t in target.tools))

    def matches(self, name: str, args: Optional[Dict[str, Any]], paths: Paths) -> bool:
        return any(t.matches(name, args, paths) for t in self.targets)

    def describe(self) -> str:
        return " or ".join(t.describe() for t in self.targets)


def parse_target(spec: Any, where: str, *, with_: Any = None, where_: Any = None) -> Any:
    """Build a target from its YAML form; ``with_``/``where_`` are rule-level extras."""
    if isinstance(spec, list) and any(isinstance(s, dict) for s in spec):
        return AnyTarget(tuple(parse_target(s, f"{where}[{i}]", with_=with_, where_=where_)
                               for i, s in enumerate(spec)))
    if isinstance(spec, dict):
        unknown = set(spec) - {"tool", "with", "where", "exists"}
        if unknown:
            raise RuleError(f"{where}: unknown key(s) {sorted(unknown)} "
                            "(expected tool, with, where, exists)")
        exists = spec.get("exists")
        if exists is not None and (not isinstance(exists, bool) or not (spec.get("where") or where_)):
            raise RuleError(f"{where}.exists: must be true or false, next to a 'where'")
        if "tool" not in spec:
            raise RuleError(f"{where}: a target needs 'tool'")
        tools = _names(spec["tool"], where)
        w = {**_mapping(spec.get("with"), f"{where}.with"), **_mapping(with_, "with")}
        g = {**_patterns(spec.get("where"), f"{where}.where"), **_patterns(where_, "where")}
        return Target(tools, w, g, exists)
    return Target(_names(spec, where), _mapping(with_, "with"), _patterns(where_, "where"))


def _names(spec: Any, where: str) -> Tuple[str, ...]:
    names = [spec] if isinstance(spec, str) else spec
    if not isinstance(names, (list, tuple)) or not names or not all(
            isinstance(n, str) and n for n in names):
        raise RuleError(f"{where}: expected a tool name or a list of tool names, got {spec!r}")
    return tuple(names)


def _mapping(spec: Any, where: str) -> Dict[str, Any]:
    if spec is None:
        return {}
    if not isinstance(spec, dict):
        raise RuleError(f"{where}: expected a mapping of argument name to value")
    return dict(spec)


def _patterns(spec: Any, where: str) -> Dict[str, Tuple[str, ...]]:
    out: Dict[str, Tuple[str, ...]] = {}
    for key, value in _mapping(spec, where).items():
        values = [value] if isinstance(value, str) else value
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            raise RuleError(f"{where}.{key}: expected a glob pattern or a list of them")
        out[key] = tuple(values)
    return out


def _equal(expected: Any, actual: Any) -> bool:
    if isinstance(actual, list) and not isinstance(expected, list):
        return any(_equal(expected, a) for a in actual)
    if isinstance(expected, bool) or isinstance(actual, bool):
        return bool(actual) == bool(expected) if actual is not None else expected is False
    if isinstance(expected, (int, float)) and isinstance(actual, str):
        return actual.strip() == str(expected)
    return expected == actual


def _strings(value: Any) -> List[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [s for v in value for s in _strings(v)]
    return []


def _glob(value: str, pattern: str, paths: Paths) -> bool:
    for candidate in _forms(value, paths, basename="/" not in pattern):
        if fnmatch.fnmatchcase(candidate, pattern):
            return True
    return False


def _absolute(value: str, paths: Paths) -> str:
    return os.path.normpath(os.path.join(paths.cwd or os.getcwd(), os.path.expanduser(value)))


def _forms(value: str, paths: Paths, basename: bool) -> Sequence[str]:
    forms = [value]
    if value and not value.startswith(("-", "http://", "https://")):
        absolute = _absolute(value, paths)
        forms.append(absolute)
        if paths.root and (absolute == paths.root or absolute.startswith(paths.root + os.sep)):
            forms.append(os.path.relpath(absolute, paths.root))
        if basename:
            forms.append(os.path.basename(absolute))
    return forms
