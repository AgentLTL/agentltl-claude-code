"""
agentltl_cc/cli.py – the ``agentltl`` command.

    agentltl validate [FILE]             compile the rules; list them or the problems
    agentltl check STEP...               replay steps through the rules in a fresh session
    agentltl translate COMMAND           the structured calls a shell command stands for
    agentltl tools [PATTERN]             tool names and arguments rules can refer to
    agentltl trace [--session ID]        what the guard recorded: this session, and the project
    agentltl reset [--session ID]        forget this session's trace
    agentltl reset --project             forget the project's trace

A ``check`` step is a shell command (``"git push -f"``) or another tool as
``'Edit {"file_path": ".env"}'``. Prefix a step with what you expect (``deny: git push``,
``allow: pytest``) and ``check`` exits 1 when a step does not do that. Steps that are
allowed count as executed for the steps after them.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import logging
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

from . import store
from .match import Paths
from .rules import MODE_HELP, RuleFileError, RuleSet, load, rule_files

_EXPECT = re.compile(r"^(allow|deny|ask|stop|note)\s*:\s*", re.I)
_TOOL_STEP = re.compile(r"^([A-Z][A-Za-z0-9_]*|mcp__[A-Za-z0-9_]+)\s+(\{.*\})\s*$", re.S)


def main(argv: Optional[List[str]] = None) -> int:
    logging.getLogger("agentltl").setLevel(logging.ERROR)
    parser = argparse.ArgumentParser(prog="agentltl", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("validate", help="compile the rule files and list the rules")
    p.add_argument("files", nargs="*", help="rule files (default: the ones that apply here)")

    p = sub.add_parser("check", help="replay steps through the rules")
    p.add_argument("steps", nargs="+", metavar="STEP")
    p.add_argument("--rules", action="append", default=[], metavar="FILE",
                   help="use only these rule files (repeatable)")
    p.add_argument("--add", action="append", default=[], metavar="FILE",
                   help="add these rule files (e.g. a draft) to the ones that apply here")
    p.add_argument("--auto", action="store_true", help="as if Claude Code ran in auto mode")
    p.add_argument("--json", action="store_true", help="machine-readable output")

    p = sub.add_parser("translate", help="show the structured calls of a shell command")
    p.add_argument("command")

    p = sub.add_parser("tools", help="tool names and arguments that rules can refer to")
    p.add_argument("pattern", nargs="?", default="*",
                   help="glob on tool names, e.g. 'git_*' (default: all)")

    for name in ("trace", "reset"):
        p = sub.add_parser(name, help="show" if name == "trace" else "forget"
                           " what the guard recorded")
        p.add_argument("--session", help="session id (default: the latest one here)")
        if name == "reset":
            p.add_argument("--project", action="store_true",
                           help="forget the project memory instead of the session's")

    args = parser.parse_args(argv)
    try:
        return _COMMANDS[args.cmd](args)
    except RuleFileError as exc:
        print("Rule file problems:", file=sys.stderr)
        for problem in exc.problems:
            print(f"  {problem}", file=sys.stderr)
        return 2


def _here() -> Tuple[str, str]:
    cwd = os.getcwd()
    return cwd, os.environ.get("CLAUDE_PROJECT_DIR") or _git_root(cwd) or cwd


def _git_root(path: str) -> Optional[str]:
    while True:
        if os.path.exists(os.path.join(path, ".git")):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent


def _ruleset(files: List[str], add: List[str] = ()) -> RuleSet:
    cwd, root = _here()
    files = list(files) or rule_files(cwd, root)
    files += [f for f in add if f not in files]
    return load(files, Paths(cwd, root))


# ── commands ──────────────────────────────────────────────────────────────────

def _validate(args: argparse.Namespace) -> int:
    ruleset = _ruleset(args.files)
    if not ruleset.files:
        print("No AGENTLTL.yaml here (nor in ~/.claude). Nothing is enforced.")
        return 0
    print(f"OK: {len(ruleset.rules)} rule(s) from {', '.join(ruleset.files)}")
    for r in ruleset.rules:
        memory = ", project memory" if r.scope == "project" else ""
        print(f"  {r.id} [{r.mode}{memory}]: {r.summary}")
        if r.why:
            print(f"      why: {r.why}")
    _warn(ruleset)
    s = ruleset.settings
    print(f"settings: default mode {s.mode}, default memory {s.scope}, retries {s.retries}, "
          "unparseable commands: "
          f"{s.unparseable_interactive} (interactive) / {s.unparseable_auto} (auto mode)")
    return 0


def _check(args: argparse.Namespace) -> int:
    from .guard import Guard

    cwd, root = _here()
    ruleset = _ruleset(args.rules, args.add)
    guard = Guard(ruleset, Paths(cwd, root))
    guard.restore({}, {})
    if not args.json:
        _warn(ruleset)
    rows, failed = [], False
    for i, raw in enumerate(args.steps, 1):
        expect = None
        m = _EXPECT.match(raw)
        if m:
            expect, raw = m.group(1).lower(), raw[m.end():]
        tool, tool_input = _step(raw)
        verdict = guard.decide(tool, tool_input, auto=args.auto)
        got = verdict.action
        if got == "none":
            got = "note" if verdict.context else "allow"
        if got in ("allow", "note"):
            guard.record(tool, tool_input, f"step{i}", "")
        ok = expect is None or expect == got or (expect == "allow" and got == "note")
        failed |= not ok
        rows.append({"step": i, "call": raw, "result": got, "expected": expect, "ok": ok,
                     "rule": verdict.rule, "calls": verdict.calls,
                     "reason": verdict.reason or verdict.context})
    if args.json:
        print(json.dumps(rows, indent=2))
        return 1 if failed else 0
    for row in rows:
        mark = "" if row["expected"] is None else ("  ok" if row["ok"] else
                                                   f"  EXPECTED {row['expected'].upper()}")
        rule = f"  [{row['rule']}]" if row["rule"] else ""
        print(f"{row['step']:>2}. {row['result'].upper():<5} {row['call']}{rule}{mark}")
        names = [c["tool_name"] for c in row["calls"]]
        if names and names != [row["call"].split()[0]]:
            print(f"      calls: {', '.join(names)}")
        if row["result"] not in ("allow",) and row["reason"]:
            for line in row["reason"].splitlines():
                print(f"      | {line}")
    return 1 if failed else 0


def _warn(ruleset: RuleSet) -> None:
    from .guard import lint
    for warning in lint(ruleset):
        print(f"WARNING {warning}")


def _step(raw: str) -> Tuple[str, Dict[str, Any]]:
    m = _TOOL_STEP.match(raw.strip())
    if m:
        try:
            return m.group(1), json.loads(m.group(2))
        except ValueError as exc:
            raise SystemExit(f"step {raw!r}: invalid JSON input: {exc}")
    return "Bash", {"command": raw}


def _translate(args: argparse.Namespace) -> int:
    from cli_to_tools import TranslationError

    from .guard import translator_for
    try:
        calls = translator_for(_ruleset([])).translate(args.command)
    except TranslationError as exc:
        print(f"Cannot translate: {exc}", file=sys.stderr)
        return 1
    for c in calls:
        flags = [k for k in ("conditional", "repeated") if c.meta.get(k)]
        extra = f"  ({', '.join(flags)})" if flags else ""
        print(f"{c.name} {json.dumps(c.args)}{extra}")
    return 0


def _tools(args: argparse.Namespace) -> int:
    from .guard import translator_for
    schemas = translator_for(_ruleset([])).registry.tool_schemas()
    shown = 0
    for s in schemas:
        if not fnmatch.fnmatchcase(s["name"], args.pattern):
            continue
        props = s.get("parameters", {}).get("properties", {})
        params = ", ".join(f"{k}: {v.get('type', 'string')}" for k, v in props.items())
        print(f"{s['name']}({params})")
        shown += 1
    if shown == 0:
        print(f"No tool matches {args.pattern!r}. Commands without a spec become a tool named "
              "after the executable with a single 'argv' list.")
    print("\nClaude Code's own tools keep their names (Edit, Write, Read, WebFetch, mcp__...) "
          "with their input fields as arguments (Edit/Write: file_path). Shell output "
          "redirections appear as 'redirect_to'.")
    return 0


def _session(args: argparse.Namespace) -> Optional[str]:
    sid = args.session or store.latest_session(_here()[1])
    if sid is None:
        print("No recorded session for this project.", file=sys.stderr)
    return sid


def _trace(args: argparse.Namespace) -> int:
    root = _here()[1]
    project = store.read_project(root).get("trace") or []
    print(f"project {root}: {len(project)} recorded call(s), across sessions")
    sid = args.session or store.latest_session(root)
    if sid is None:
        print("No recorded session for this project.")
        return 0
    state = store.read(sid)
    trace = state.get("trace") or []
    print(f"session {sid}: {len(trace)} recorded call(s)")
    for i, c in enumerate(trace[-50:], max(1, len(trace) - 49)):
        args_ = {k: v for k, v in (c.get("arguments") or {}).items()
                 if v not in (None, False, [], "")}
        print(f"  {i:>3}. {c.get('tool_name')} {json.dumps(args_)[:120]}")
    decisions = state.get("decisions") or []
    if decisions:
        print(f"\nlast {min(10, len(decisions))} intervention(s):")
        for d in decisions[-10:]:
            what = d["input"].get("command") or d["input"].get("file_path") or ""
            print(f"  {d['action'].upper():<5} [{d['rule'] or 'not checked'}] {d['tool']} {str(what)[:100]}")
    return 0


def _reset(args: argparse.Namespace) -> int:
    if args.project:
        root = _here()[1]
        store.reset_project(root)
        print(f"Project {root}: trace and counters cleared.")
        return 0
    sid = _session(args)
    if sid is None:
        return 1
    store.reset(sid)
    print(f"Session {sid}: trace and counters cleared.")
    return 0


_COMMANDS = {"validate": _validate, "check": _check, "translate": _translate, "tools": _tools,
             "trace": _trace, "reset": _reset}

__all__ = ["main", "MODE_HELP"]

if __name__ == "__main__":
    sys.exit(main())
