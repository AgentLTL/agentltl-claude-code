"""
agentltl_guard/store.py – per-session state on disk.

Each hook call is a fresh process, so the session's trace and enforcer counters live in
``<state dir>/<session id>.json``. Claude Code may run tool calls in parallel, so every
read-modify-write holds an exclusive lock on ``<file>.lock``.

State dir: ``$AGENTLTL_GUARD_STATE``, else ``sessions/`` next to the plugin's virtualenv
(``$CLAUDE_PLUGIN_DATA/venv`` → ``$CLAUDE_PLUGIN_DATA/sessions``, so the hook and the
``agentltl`` CLI agree without sharing an environment), else ``~/.cache/agentltl-guard/sessions``.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import sys
import tempfile
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional


def state_dir() -> str:
    if os.environ.get("AGENTLTL_GUARD_STATE"):
        return os.environ["AGENTLTL_GUARD_STATE"]
    if os.path.basename(sys.prefix) == "venv":
        return os.path.join(os.path.dirname(sys.prefix), "sessions")
    return os.path.join(os.path.expanduser("~"), ".cache", "agentltl-guard", "sessions")


def session_path(session_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "default")[:128]
    return os.path.join(state_dir(), f"{safe}.json")


def read(session_id: str) -> Dict[str, Any]:
    try:
        with open(session_path(session_id), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


@contextmanager
def locked(session_id: str) -> Iterator[Dict[str, Any]]:
    """Yield the session state for update; whatever it holds on exit is written back."""
    path = session_path(session_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = read(session_id)
        yield state
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, default=str)
        os.replace(tmp, path)


def latest_session(cwd: Optional[str] = None) -> Optional[str]:
    """The most recently updated session, optionally only one whose project contains *cwd*."""
    folder = state_dir()
    try:
        names = [n for n in os.listdir(folder) if n.endswith(".json")]
    except OSError:
        return None
    names.sort(key=lambda n: os.path.getmtime(os.path.join(folder, n)), reverse=True)
    for name in names:
        sid = name[:-5]
        project = read(sid).get("project_dir")
        if cwd is None or (project and (cwd == project or cwd.startswith(project + os.sep))):
            return sid
    return None


def reset(session_id: str) -> None:
    with locked(session_id) as state:
        keep = {k: state[k] for k in ("project_dir",) if k in state}
        state.clear()
        state.update(keep)


__all__: List[str] = ["state_dir", "session_path", "read", "locked", "latest_session", "reset"]
