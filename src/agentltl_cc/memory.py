"""
agentltl_cc/memory.py – where Claude Code's memory lives.

Claude Code's memory is markdown: ``CLAUDE.md`` files, ``.claude/rules/``, and auto memory
(``~/.claude/projects/<project>/memory/*.md``). :func:`claude_sources` lists the files that
apply; splitting them into statements, the hints, and remembering a "no" are
agentltl_coding's (:mod:`agentltl_coding.memory`), re-exported here.
"""

from __future__ import annotations

import os
import re
from typing import List

from agentltl_coding.memory import *  # noqa: F401,F403  (scan, split, decline, ...)
from agentltl_coding.memory import PROJECT, USER, Source, files, nested


def project_slug(path: str) -> str:
    """The folder name Claude Code gives a project under ``~/.claude/projects``."""
    return re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(path))


def claude_sources(root: str, user_only: bool, home: str) -> List[Source]:
    """The memory files that apply in project *root*: its own, its auto memory, and the user's
    (``~/.claude/CLAUDE.md``, ``~/.claude/rules/``). With *user_only*, the user's files and the
    auto memory of sessions started in the home directory instead."""
    claude = os.path.join(home, ".claude")
    user = [Source(p, k, USER) for p, k in
            [(os.path.join(claude, "CLAUDE.md"), "claude-md")]
            + [(p, "rules") for p in files(os.path.join(claude, "rules"), "*.md")]]
    if user_only:
        return user + _auto_memory(home, home, USER)
    out: List[Source] = []
    for name, kind in (("CLAUDE.md", "claude-md"), ("CLAUDE.local.md", "claude-md"),
                       (os.path.join(".claude", "CLAUDE.md"), "claude-md"),
                       ("AGENTS.md", "agents-md"), (".cursorrules", "agents-md")):
        out.append(Source(os.path.join(root, name), kind, PROJECT))
    out += [Source(p, "rules", PROJECT)
            for p in files(os.path.join(root, ".claude", "rules"), "*.md")]
    out += [Source(p, "claude-md", PROJECT) for p in nested(root, "CLAUDE.md")]
    out += _auto_memory(root, home, PROJECT if os.path.abspath(root) != home else USER)
    return out + user


def _auto_memory(root: str, home: str, target: str) -> List[Source]:
    folder = os.path.join(home, ".claude", "projects", project_slug(root), "memory")
    return [Source(p, "auto-memory", target) for p in files(folder, "*.md")
            if os.path.basename(p) != "MEMORY.md"]          # the index repeats the files
