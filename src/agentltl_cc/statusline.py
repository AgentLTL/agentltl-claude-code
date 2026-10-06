"""
agentltl_cc/statusline.py – AgentLTL in Claude Code's status line.

Claude Code shows nothing a plugin prints at session start, and a plugin can't set the status
line itself, so ``agentltl statusline --install`` adds one to the user's settings:

    AgentLTL ● 4 rules · 2 block · 1 ask · 1 warn
    AgentLTL ○ no rules here
    AgentLTL ⚠ AGENTLTL.yaml has errors · nothing is enforced

The status line runs often, so the line is cached until a rule file changes. The settings
point at a small script in the plugin's data directory that runs whichever plugin version is
installed now (``scripts/statusline``), so plugin updates don't break it.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from agentltl_coding.rules import LIBRARY_DIR, STRENGTH, RuleFileError, load, rule_files

_GREEN, _YELLOW, _DIM, _RESET = "\033[32m", "\033[33m", "\033[2m", "\033[0m"
_CACHE_SIZE = 32
SHIM_NAME = "statusline"


def data_dir() -> str:
    return os.environ.get("CLAUDE_PLUGIN_DATA") or os.path.join(
        os.path.expanduser("~"), ".claude", "plugins", "data", "agentltl-agentltl")


def settings_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".claude", "settings.json")


# ── the line ──────────────────────────────────────────────────────────────────

def where(payload: Dict[str, Any]) -> Tuple[str, str]:
    """(cwd, project dir) from the JSON Claude Code passes a status line."""
    ws = payload.get("workspace") or {}
    cwd = ws.get("current_dir") or payload.get("cwd") or os.getcwd()
    project = ws.get("project_dir") or _git_root(cwd) or cwd
    return cwd, project


def _git_root(path: str) -> Optional[str]:
    while True:
        if os.path.exists(os.path.join(path, ".git")):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent


def line(payload: Dict[str, Any], color: bool = True, cache: Optional[str] = None) -> str:
    cwd, project = where(payload)
    files = rule_files(cwd, project)
    if not files:
        return _paint("AgentLTL ○ no rules here", _DIM, color)
    key = json.dumps([[f, *_stamp(f)] for f in files] + [_stamp(LIBRARY_DIR), color])
    cache = cache or os.path.join(data_dir(), "statusline-cache.json")
    cached = _read(cache)
    if key in cached:
        return cached[key]
    from agentltl_coding.pattern import Paths
    try:
        ruleset = load(files, Paths(cwd, project))
    except RuleFileError:
        text = _paint("AgentLTL ⚠ AGENTLTL.yaml has errors · nothing is enforced "
                      "(agentltl validate)", _YELLOW, color)
    else:
        text = summary([r.mode for r in ruleset.rules], color)
    cached[key] = text
    _write(cache, dict(list(cached.items())[-_CACHE_SIZE:]))
    return text


def summary(modes: List[str], color: bool = True) -> str:
    n = len(modes)
    if not n:
        return _paint("AgentLTL ○ 0 rules", _DIM, color)
    counts = " · ".join(f"{modes.count(m)} {m}" for m in STRENGTH if m in modes)
    dot = _paint("●", _GREEN, color)
    return f"AgentLTL {dot} {n} rule{'s' if n != 1 else ''} · {counts}"


def _paint(text: str, code: str, color: bool) -> str:
    return f"{code}{text}{_RESET}" if color else text


def _stamp(path: str) -> List[float]:
    try:
        st = os.stat(path)
        return [st.st_mtime, st.st_size]
    except OSError:
        return [0, 0]


def _read(path: str) -> Dict[str, str]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path: str, data: Dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
    except OSError:
        pass                                  # a status line without a cache still works


# ── installing it in the user's settings ──────────────────────────────────────

def install(root: str, force: bool = False, settings: Optional[str] = None,
            shim_dir: Optional[str] = None) -> Tuple[bool, str]:
    """Write the shim and point ``statusLine`` in the user's settings at it.

    Refuses (returns False) when another status line is set, unless *force*.
    """
    settings = settings or settings_path()
    shim = os.path.join(shim_dir or data_dir(), SHIM_NAME)
    data = _settings(settings)
    current = (data.get("statusLine") or {}).get("command") if isinstance(
        data.get("statusLine"), dict) else data.get("statusLine")
    if current and current != shim and not force:
        return False, (f"You already have a status line: {current}\n"
                       f"To add AgentLTL to it, have your script pass its input on and print "
                       f"the result, e.g.  echo \"$input\" | {shim}\n"
                       "Or replace yours with: agentltl statusline --install --force")
    _write_shim(shim, root)
    data["statusLine"] = {"type": "command", "command": shim}
    _write_settings(settings, data)
    return True, f"Status line set in {settings}: {shim}"


def uninstall(settings: Optional[str] = None, shim_dir: Optional[str] = None) -> Tuple[bool, str]:
    settings = settings or settings_path()
    shim = os.path.join(shim_dir or data_dir(), SHIM_NAME)
    data = _settings(settings)
    entry = data.get("statusLine")
    if not isinstance(entry, dict) or entry.get("command") != shim:
        return False, "The status line is not AgentLTL's; nothing changed."
    del data["statusLine"]
    _write_settings(settings, data)
    return True, f"AgentLTL's status line removed from {settings}."


def _write_shim(shim: str, root: str) -> None:
    with open(os.path.join(root, "scripts", "statusline"), encoding="utf-8") as fh:
        text = fh.read().replace("__AGENTLTL_ROOT__", root)
    os.makedirs(os.path.dirname(shim), exist_ok=True)
    with open(shim, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(shim, 0o755)


def _settings(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)          # an invalid file raises: never overwrite what we can't read
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    return data


def _write_settings(path: str, data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


__all__ = ["line", "summary", "where", "install", "uninstall", "data_dir", "settings_path"]
