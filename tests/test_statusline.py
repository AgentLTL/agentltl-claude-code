"""`agentltl statusline`: the rules in force, in Claude Code's status line."""

import json
import os
import subprocess

import pytest

from agentltl_cc import statusline
from agentltl_cc.rules import LIBRARY_DIR

ROOT = os.path.dirname(LIBRARY_DIR)


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "proj"
    (root / ".git").mkdir(parents=True)
    return root


def _line(root, cache):
    return statusline.line({"workspace": {"current_dir": str(root)}}, color=False,
                           cache=str(cache))


def test_counts_by_mode(project, tmp_path):
    (project / "AGENTLTL.yaml").write_text(
        "use: [no-force-push, ask-before-installing]\nrules: [{id: a, never: rm, mode: warn}]\n")
    assert _line(project, tmp_path / "c.json") == \
        "AgentLTL ● 4 rules · 1 block · 1 ask · 2 warn"      # + the built-in memory-first


def test_no_rules_and_broken_files(project, tmp_path):
    assert _line(project, tmp_path / "c.json") == "AgentLTL ○ no rules here"
    (project / "AGENTLTL.yaml").write_text("rules: [{id: x}]\n")
    assert "has errors · nothing is enforced" in _line(project, tmp_path / "c.json")


def test_cached_until_a_rule_file_changes(project, tmp_path, monkeypatch):
    rules = project / "AGENTLTL.yaml"
    rules.write_text("rules: [{id: a, never: rm}]\n")
    cache = tmp_path / "c.json"
    first = _line(project, cache)
    with monkeypatch.context() as m:
        m.setattr(statusline, "load", lambda *a: pytest.fail("not cached"))
        assert _line(project, cache) == first
    rules.write_text("rules: [{id: a, never: rm}, {id: b, never: mv, mode: log}]\n")
    os.utime(rules, (1, 1))
    assert _line(project, cache) == "AgentLTL ● 3 rules · 1 block · 1 warn · 1 log"


class TestInstall:
    def test_keeps_the_rest_of_the_settings(self, tmp_path):
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({"model": "sonnet", "enabledPlugins": {"x@y": True}}))
        ok, _ = statusline.install(ROOT, settings=str(settings), shim_dir=str(tmp_path / "d"))
        data = json.loads(settings.read_text())
        shim = str(tmp_path / "d" / "statusline")
        assert ok and data == {"model": "sonnet", "enabledPlugins": {"x@y": True},
                               "statusLine": {"type": "command", "command": shim}}
        assert os.access(shim, os.X_OK) and ROOT in open(shim).read()
        assert statusline.install(ROOT, settings=str(settings), shim_dir=str(tmp_path / "d"))[0]
        assert statusline.uninstall(str(settings), str(tmp_path / "d"))[0]
        assert json.loads(settings.read_text()) == {"model": "sonnet",
                                                     "enabledPlugins": {"x@y": True}}

    def test_never_replaces_another_status_line_unasked(self, tmp_path):
        settings = tmp_path / "settings.json"
        mine = {"statusLine": {"type": "command", "command": "~/my-line.sh"}}
        settings.write_text(json.dumps(mine))
        ok, message = statusline.install(ROOT, settings=str(settings), shim_dir=str(tmp_path))
        assert not ok and "~/my-line.sh" in message and json.loads(settings.read_text()) == mine
        assert not statusline.uninstall(str(settings), str(tmp_path))[0]
        assert statusline.install(ROOT, force=True, settings=str(settings),
                                  shim_dir=str(tmp_path))[0]

    def test_an_unreadable_settings_file_is_left_alone(self, tmp_path):
        settings = tmp_path / "settings.json"
        settings.write_text("{not json")
        with pytest.raises(ValueError):
            statusline.install(ROOT, settings=str(settings), shim_dir=str(tmp_path))
        assert settings.read_text() == "{not json"


def test_the_shim_runs_the_cli(project, tmp_path):
    statusline.install(ROOT, settings=str(tmp_path / "s.json"), shim_dir=str(tmp_path / "d"))
    (project / "AGENTLTL.yaml").write_text("rules: [{id: a, never: rm}]\n")
    env = {**os.environ, "HOME": str(tmp_path / "home"),
           "AGENTLTL_CC_PYTHON": os.sys.executable}
    out = subprocess.run([str(tmp_path / "d" / "statusline")], text=True, capture_output=True,
                         env=env, timeout=60,
                         input=json.dumps({"workspace": {"current_dir": str(project)}}))
    assert "AgentLTL" in out.stdout and "2 rules · 1 block · 1 warn" in out.stdout, out.stderr
