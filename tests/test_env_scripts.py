"""scripts/env.sh and scripts/setup.sh must find a virtualenv's interpreter whatever its layout:
bin/python on Linux and macOS, Scripts/python.exe on Windows (Git Bash), and must not rely on
`python3` existing. These tests stub the interpreters, so nothing is installed."""

import os
import pathlib
import shutil
import subprocess

import pytest

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash")
# These strip PATH down to a stub directory; on Windows bash then loses its DLLs and
# `python` is a real python.exe, not a link. windows.yml covers the same ground for real.
WINDOWS_PATH_REASON = "needs a PATH of only stubs, which Git Bash on Windows cannot run with"


@pytest.fixture
def plugin(tmp_path):
    """The scripts copied to a clean directory. A checkout can have its own .venv, which
    env_python prefers, and that would answer in place of the stub environment under test."""
    root = tmp_path / "plugin"
    shutil.copytree(os.path.join(SRC, "scripts"), root / "scripts")
    shutil.copy(os.path.join(SRC, "vendor.lock"), root / "vendor.lock")
    return root


def _stub(path, body="exit 0"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/usr/bin/env bash\n" + body + "\n")
    path.chmod(0o755)
    return path


def _env_sh(plugin, snippet, env=None):
    script = f'root="{plugin}"; source "{plugin}/scripts/env.sh"; {snippet}'
    return subprocess.run([BASH, "-c", script], capture_output=True, text=True,
                          env={**os.environ, **(env or {})})


@pytest.mark.parametrize("rel", ["bin/python", "Scripts/python.exe"])
def test_venv_python_finds_either_layout(plugin, tmp_path, rel):
    py = _stub(tmp_path / "venv" / rel)
    out = _env_sh(plugin, f'env_venv_python "{tmp_path / "venv"}"')
    assert out.returncode == 0
    assert pathlib.Path(out.stdout.strip()) == py


def test_venv_python_is_empty_for_an_unbuilt_venv(plugin, tmp_path):
    (tmp_path / "venv").mkdir()
    out = _env_sh(plugin, f'env_venv_python "{tmp_path / "venv"}"')
    assert out.returncode != 0 and out.stdout == ""


def test_env_python_uses_the_windows_layout(plugin, tmp_path):
    py = _stub(tmp_path / "venv" / "Scripts" / "python.exe")
    shutil.copy(plugin / "vendor.lock", tmp_path / "venv" / "vendor.lock")   # setup.sh finished
    out = _env_sh(plugin, "env_python", {"AGENTLTL_CC_VENV": str(tmp_path / "venv")})
    assert pathlib.Path(out.stdout.strip()) == py


def test_a_half_built_venv_is_not_used(plugin, tmp_path):
    """A failed setup leaves an interpreter but no dependencies and no vendor.lock."""
    _stub(tmp_path / "venv" / "Scripts" / "python.exe")
    out = _env_sh(plugin, "env_python", {"AGENTLTL_CC_VENV": str(tmp_path / "venv")})
    assert out.returncode != 0 and out.stdout == ""


def test_build_finishes_a_half_built_venv(plugin, tmp_path):
    log = tmp_path / "calls"
    venv = tmp_path / "venv"
    _stub(venv / "Scripts" / "python.exe", f'echo "$@" >> "{log}"')
    out = _env_sh(plugin, "env_build", {"AGENTLTL_CC_VENV": str(venv)})
    assert out.returncode == 0, out.stderr
    assert "-m pip install -q --upgrade pip" in log.read_text()
    assert (venv / "vendor.lock").is_file()


@pytest.mark.skipif(os.name == "nt", reason=WINDOWS_PATH_REASON)
def test_find_python_skips_a_python3_that_does_not_run(plugin, tmp_path):
    """The Microsoft Store's python3 stub exits non-zero: fall through to `python`."""
    bin_dir = tmp_path / "bin"
    _stub(bin_dir / "python3", "echo 'Python was not found' >&2; exit 9")
    real = shutil.which("python3") or shutil.which("python")
    assert real, "needs a Python 3 to stand in for `python`"
    os.symlink(real, bin_dir / "python")
    out = _env_sh(plugin, "env_find_python", {"PATH": f"{bin_dir}:/usr/bin:/bin"})
    assert out.stdout.strip() == "python"


def test_setup_drives_pip_through_the_windows_interpreter(plugin, tmp_path):
    log = tmp_path / "calls"
    venv = tmp_path / "venv"
    _stub(venv / "Scripts" / "python.exe", f'echo "$@" >> "{log}"')
    out = subprocess.run([BASH, str(plugin / "scripts" / "setup.sh")], capture_output=True,
                         text=True, env={**os.environ, "AGENTLTL_CC_VENV": str(venv)})
    assert out.returncode == 0, out.stderr
    calls = [c for c in log.read_text().splitlines() if c.startswith("-m pip install")]
    assert calls[0].startswith("-m pip install -q --upgrade pip")
    assert all(c.startswith("-m pip install -q ") and "--no-input" in c for c in calls)
    assert (venv / "vendor.lock").is_file()


@pytest.mark.skipif(os.name == "nt", reason=WINDOWS_PATH_REASON)
def test_setup_says_so_when_there_is_no_python(plugin, tmp_path):
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("bash", "dirname", "env"):
        os.symlink(shutil.which(name), tools / name)
    out = subprocess.run([str(tools / "bash"), str(plugin / "scripts" / "setup.sh")],
                         capture_output=True, text=True,
                         env={"PATH": str(tools), "AGENTLTL_CC_VENV": str(tmp_path / "venv")})
    assert out.returncode == 1
    assert "needs Python 3.10" in out.stderr
    assert not (tmp_path / "venv").exists()


def test_a_marker_from_other_pins_is_not_built(plugin, tmp_path):
    """A pin change must rebuild, also in a fixed directory such as <plugin>/.venv."""
    venv = tmp_path / "venv"
    _stub(venv / "Scripts" / "python.exe")
    (venv / "vendor.lock").write_text("an older set of pins\n")
    out = _env_sh(plugin, "env_python", {"AGENTLTL_CC_VENV": str(venv)})
    assert out.returncode != 0 and out.stdout == ""


def test_setup_bootstraps_pip_in_a_venv_that_has_none(plugin, tmp_path):
    """`python -m venv` creates the interpreter first; an interruption leaves it without pip."""
    log = tmp_path / "calls"
    venv = tmp_path / "venv"
    _stub(venv / "Scripts" / "python.exe",
          f'echo "$@" >> "{log}"\n[[ "$*" == "-m pip --version" ]] && exit 1\nexit 0')
    out = subprocess.run([BASH, str(plugin / "scripts" / "setup.sh")], capture_output=True,
                         text=True, env={**os.environ, "AGENTLTL_CC_VENV": str(venv)})
    assert out.returncode == 0, out.stderr
    calls = log.read_text().splitlines()
    upgrade = next(i for i, c in enumerate(calls) if c.startswith("-m pip install -q --upgrade pip"))
    assert calls.index("-m ensurepip --upgrade") < upgrade


def test_a_failed_setup_does_not_leave_the_marker_of_an_earlier_one(plugin, tmp_path):
    venv = tmp_path / "venv"
    _stub(venv / "Scripts" / "python.exe", 'case "$*" in *"pip install"*) exit 1;; esac')
    shutil.copy(plugin / "vendor.lock", venv / "vendor.lock")
    out = subprocess.run([BASH, str(plugin / "scripts" / "setup.sh")], capture_output=True,
                         text=True, env={**os.environ, "AGENTLTL_CC_VENV": str(venv)})
    assert out.returncode != 0
    assert not (venv / "vendor.lock").exists()


# ── a venv that no longer works is noticed, rebuilt once, and reported ────────

def _built_venv(plugin, tmp_path, body="exit 0"):
    """A finished venv (marker copied in) whose python is a stub running *body*."""
    venv = tmp_path / "venv"
    py = _stub(venv / "bin" / "python", body)
    shutil.copy(plugin / "vendor.lock", venv / "vendor.lock")
    return venv, py


@pytest.mark.skipif(os.name == "nt", reason=WINDOWS_PATH_REASON)
class TestBrokenEnvironment:
    def test_heal_drops_the_marker_of_a_python_that_cannot_start(self, plugin, tmp_path):
        venv, py = _built_venv(plugin, tmp_path, "echo 'Could not import runpy module' >&2; exit 1")
        out = _env_sh(plugin, f'env_heal "{py}"', {"AGENTLTL_CC_VENV": str(venv)})
        assert out.returncode == 0
        assert not (venv / "vendor.lock").exists()

    def test_heal_leaves_a_python_that_works(self, plugin, tmp_path):
        """The failure was the guard's own: rebuilding the environment would not help."""
        venv, py = _built_venv(plugin, tmp_path)
        out = _env_sh(plugin, f'env_heal "{py}"', {"AGENTLTL_CC_VENV": str(venv)})
        assert out.returncode == 1 and (venv / "vendor.lock").exists()

    def test_heal_leaves_a_test_override_alone(self, plugin, tmp_path):
        venv, py = _built_venv(plugin, tmp_path, "exit 1")
        out = _env_sh(plugin, f'env_heal "{py}"',
                      {"AGENTLTL_CC_VENV": str(venv), "AGENTLTL_CC_PYTHON": str(py)})
        assert out.returncode == 1 and (venv / "vendor.lock").exists()

    def test_a_removed_base_interpreter_means_not_built(self, plugin, tmp_path):
        venv, py = _built_venv(plugin, tmp_path)
        (venv / ".base").write_text(str(tmp_path / "gone" / "python3") + "\n")
        out = _env_sh(plugin, "env_python", {"AGENTLTL_CC_VENV": str(venv)})
        assert out.returncode != 0 and out.stdout == ""

    @pytest.mark.parametrize("imports_work", [True, False])
    def test_an_upgraded_base_interpreter_is_checked_once(self, plugin, tmp_path, imports_work):
        venv, py = _built_venv(plugin, tmp_path, "exit 0" if imports_work else "exit 1")
        base = _stub(tmp_path / "base" / "python3")
        (venv / ".base").write_text(f"{base}\n")
        os.utime(venv / ".base", (1, 1))                  # the record is older than the base
        out = _env_sh(plugin, "env_python", {"AGENTLTL_CC_VENV": str(venv)})
        assert (out.returncode == 0) == imports_work
        if imports_work:                                  # checked: the record is current again
            assert (venv / ".base").stat().st_mtime > 1

    def test_a_venv_without_a_base_record_is_still_used(self, plugin, tmp_path):
        """Built before the record existed."""
        venv, py = _built_venv(plugin, tmp_path)
        out = _env_sh(plugin, "env_python", {"AGENTLTL_CC_VENV": str(venv)})
        assert pathlib.Path(out.stdout.strip()) == py

    def test_a_failed_build_is_not_repeated_by_every_hook(self, plugin, tmp_path):
        log = tmp_path / "calls"
        venv = tmp_path / "venv"
        _stub(venv / "bin" / "python",
              f'echo "$@" >> "{log}"\ncase "$*" in *"pip install"*) exit 1;; esac')
        env = {"AGENTLTL_CC_VENV": str(venv)}
        assert _env_sh(plugin, "env_build", env).returncode != 0
        assert (tmp_path / ".build-failed").exists()
        calls = log.read_text()
        again = _env_sh(plugin, "env_build", env)
        assert again.returncode == 1 and "not trying again yet" in again.stderr
        assert log.read_text() == calls                   # no second pip, no second git

    def test_a_lock_left_by_a_dead_build_is_taken_over_at_once(self, plugin, tmp_path):
        venv, py = _built_venv(plugin, tmp_path)
        dead = subprocess.Popen(["true"])
        dead.wait()
        lock = tmp_path / ".setup.lock"
        lock.mkdir()
        (lock / "pid").write_text(str(dead.pid))
        out = subprocess.run([BASH, "-c", f'root="{plugin}"; source "{plugin}/scripts/env.sh"; env_build'],
                             capture_output=True, text=True, timeout=30,
                             env={**os.environ, "AGENTLTL_CC_VENV": str(venv)})
        assert out.returncode == 0 and not lock.exists()

    def test_doctor_says_what_is_wrong(self, plugin, tmp_path):
        venv, py = _built_venv(plugin, tmp_path, "echo 'Could not import runpy module' >&2; exit 1")
        out = _env_sh(plugin, "env_doctor", {"AGENTLTL_CC_VENV": str(venv), "PYTHONHOME": "/x"})
        assert out.returncode == 1
        assert "imports:       FAILED" in out.stdout and "Could not import runpy" in out.stdout
        assert "PYTHONHOME=/x" in out.stdout and "To rebuild: rm -rf" in out.stdout

    def test_doctor_on_a_working_environment(self, plugin, tmp_path):
        venv, py = _built_venv(plugin, tmp_path, "echo 3.12.0")
        out = _env_sh(plugin, "env_doctor", {"AGENTLTL_CC_VENV": str(venv)})
        assert out.returncode == 0 and "verdict:       working" in out.stdout

