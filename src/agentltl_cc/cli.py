"""
agentltl_cc/cli.py – the ``agentltl`` command of the Claude Code plugin.

Everything agentltl_coding's command does (validate, check, translate, tools, trace, reset,
library, use/unuse, disable/enable, and memory scan/decline/forget over CLAUDE.md and auto
memory), plus what is Claude Code's own:

    agentltl statusline [--install]      the status line (reads Claude Code's JSON on stdin)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Callable, Dict, List, Optional

from . import CLAUDE_CODE  # noqa: F401  (configures the harness)
from agentltl_coding.cli import main as _main

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main(argv: Optional[List[str]] = None) -> int:
    return _main(argv, extend=_extend)


def _extend(sub: Any, commands: Dict[str, Callable[[argparse.Namespace], int]]) -> None:
    p = sub.add_parser("statusline", help="AgentLTL's status line for Claude Code")
    p.add_argument("--install", action="store_true",
                   help="show it in Claude Code's status line (edits ~/.claude/settings.json)")
    p.add_argument("--force", action="store_true", help="with --install: replace a status line")
    p.add_argument("--uninstall", action="store_true", help="remove AgentLTL's status line")

    commands["statusline"] = _statusline


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


__all__ = ["main"]

if __name__ == "__main__":
    sys.exit(main())
