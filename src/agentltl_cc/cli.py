"""
agentltl_cc/cli.py – the ``agentltl`` command of the Claude Code plugin.

Everything agentltl_coding's command does (validate, check, translate, tools, trace, reset,
library, use/unuse, disable/enable), plus what is Claude Code's own:

    agentltl memory scan [FILE...]       statements in CLAUDE.md and memory that could be rules
    agentltl memory decline ID...        don't propose these statements again (forget: undo)
    agentltl statusline [--install]      the status line (reads Claude Code's JSON on stdin)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Callable, Dict, List, Optional

from . import CLAUDE_CODE  # noqa: F401  (configures the harness)
from agentltl_coding.cli import _here, _ruleset
from agentltl_coding.cli import main as _main
from agentltl_coding.rules import RuleFileError, RuleSet

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main(argv: Optional[List[str]] = None) -> int:
    return _main(argv, extend=_extend)


def _extend(sub: Any, commands: Dict[str, Callable[[argparse.Namespace], int]]) -> None:
    p = sub.add_parser("statusline", help="AgentLTL's status line for Claude Code")
    p.add_argument("--install", action="store_true",
                   help="show it in Claude Code's status line (edits ~/.claude/settings.json)")
    p.add_argument("--force", action="store_true", help="with --install: replace a status line")
    p.add_argument("--uninstall", action="store_true", help="remove AgentLTL's status line")

    p = sub.add_parser("memory", help="find the rules in CLAUDE.md and Claude's memory")
    msub = p.add_subparsers(dest="memory_cmd", required=True)
    m = msub.add_parser("scan", help="statements that could become rules")
    m.add_argument("files", nargs="*", help="only these files (default: the memory that "
                   "applies here)")
    m.add_argument("--user", action="store_true",
                   help="the user's memory (~/.claude/CLAUDE.md, rules, home-directory sessions)")
    m.add_argument("--all", action="store_true", help="also statements already covered or declined")
    m.add_argument("--json", action="store_true", help="machine-readable output")
    for name, what in (("decline", "don't propose these statements again"),
                       ("forget", "undo `decline`")):
        m = msub.add_parser(name, help=what)
        m.add_argument("ids", nargs="+", metavar="ID")
    commands["statusline"] = _statusline
    commands["memory"] = _memory


def _statusline(args: argparse.Namespace) -> int:
    from . import statusline

    if args.install or args.uninstall:
        if args.install:
            ok, message = statusline.install(PLUGIN_ROOT, force=args.force)
        else:
            ok, message = statusline.uninstall()
        print(message, file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1
    try:
        payload = json.loads(sys.stdin.read() or "{}") if not sys.stdin.isatty() else {}
    except ValueError:
        payload = {}
    try:
        print(statusline.line(payload if isinstance(payload, dict) else {}))
    except Exception as exc:          # a status line must print something, never a traceback
        print(f"AgentLTL ⚠ {type(exc).__name__}")
    return 0


def _memory(args: argparse.Namespace) -> int:
    from . import memory

    root = _here()[1]
    if args.memory_cmd in ("decline", "forget"):
        have = getattr(memory, args.memory_cmd)(root, args.ids)
        print(f"Declined for {root}: {', '.join(have) or '(none)'}")
        return 0
    from agentltl_coding.guard import translator_for
    try:
        ruleset = _ruleset([])
    except RuleFileError:
        ruleset = None
    translator = translator_for(ruleset or RuleSet())
    result = memory.scan(root, ruleset, translator, user_only=args.user, files=args.files)
    if not args.all:
        result["statements"] = [s for s in result["statements"]
                                if s["status"] == "new" and s["candidate"]]
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    c = result["counts"]
    print(f"{len(result['sources'])} memory file(s), {c['statements']} statement(s): "
          f"{c['candidates']} new that may be rules, {c['covered']} covered by a rule, "
          f"{c['declined']} declined." + ("" if args.all else " --all lists every statement."))
    last = None
    for st in result["statements"]:
        if st["file"] != last:
            last = st["file"]
            print(f"\n{last}  (rules go in the {st['target']} file)")
        mark = "" if st["status"] == "new" else f" [{st['status']}]"
        mark += "" if st["candidate"] or not args.all else " [not a candidate]"
        text = " ⏎ ".join(line.strip() for line in st["text"].splitlines())
        text = text if len(text) <= 110 else text[:107] + "..."
        print(f"  {st['id']} :{st['line']}{mark} {text}")
        for cmd in st["commands"]:
            names = ", ".join(c["name"] for c in cmd.get("calls", []))
            spec = f"  (no spec: {', '.join(cmd['needs_spec'])})" if cmd.get("needs_spec") else ""
            print(f"      `{cmd['text']}` -> {names}{spec}")
    return 0


__all__ = ["main"]

if __name__ == "__main__":
    sys.exit(main())
