"""scripts/env.sh and scripts/setup.sh must find a virtualenv's interpreter whatever its layout:
bin/python on Linux and macOS, Scripts/python.exe on Windows (Git Bash), and must not rely on
`python3` existing. These tests stub the interpreters, so nothing is installed."""

import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash")


def _stub(path, body="exit 0"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/usr/bin/env bash\n" + body + "\n")
    path.chmod(0o755)
    return path


def _env_sh(snippet, env=None):
    script = f'root="{ROOT}"; source "{ROOT}/scripts/env.sh"; {snippet}'
    return subprocess.run([BASH, "-c", script], capture_output=True, text=True,
                          env={**os.environ, **(env or {})})


@pytest.mark.parametrize("rel", ["bin/python", "Scripts/python.exe"])
def test_venv_python_finds_either_layout(tmp_path, rel):
    py = _stub(tmp_path / "venv" / rel)
    out = _env_sh(f'env_venv_python "{tmp_path / "venv"}"')
    assert out.returncode == 0
    assert out.stdout.strip() == str(py)


def test_venv_python_is_empty_for_an_unbuilt_venv(tmp_path):
    (tmp_path / "venv").mkdir()
    out = _env_sh(f'env_venv_python "{tmp_path / "venv"}"')
    assert out.returncode != 0 and out.stdout == ""


def test_env_python_uses_the_windows_layout(tmp_path):
    py = _stub(tmp_path / "venv" / "Scripts" / "python.exe")
    out = _env_sh("env_python", {"AGENTLTL_CC_VENV": str(tmp_path / "venv")})
    assert out.stdout.strip() == str(py)


def test_find_python_skips_a_python3_that_does_not_run(tmp_path):
    """The Microsoft Store's python3 stub exits non-zero: fall through to `python`."""
    bin_dir = tmp_path / "bin"
    _stub(bin_dir / "python3", "echo 'Python was not found' >&2; exit 9")
    real = shutil.which("python3") or shutil.which("python")
    assert real, "needs a Python 3 to stand in for `python`"
    os.symlink(real, bin_dir / "python")
    out = _env_sh("env_find_python", {"PATH": f"{bin_dir}:/usr/bin:/bin"})
    assert out.stdout.strip() == "python"


def test_setup_drives_pip_through_the_windows_interpreter(tmp_path):
    log = tmp_path / "calls"
    venv = tmp_path / "venv"
    _stub(venv / "Scripts" / "python.exe", f'echo "$@" >> "{log}"')
    out = subprocess.run([BASH, f"{ROOT}/scripts/setup.sh"], capture_output=True, text=True,
                         env={**os.environ, "AGENTLTL_CC_VENV": str(venv)})
    assert out.returncode == 0, out.stderr
    calls = log.read_text().splitlines()
    assert calls[0] == "-m pip install -q --upgrade pip"
    assert all(c.startswith("-m pip install -q ") for c in calls)
    assert (venv / "vendor.lock").is_file()


def test_setup_says_so_when_there_is_no_python(tmp_path):
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("bash", "dirname", "env"):
        os.symlink(shutil.which(name), tools / name)
    out = subprocess.run([str(tools / "bash"), f"{ROOT}/scripts/setup.sh"], capture_output=True,
                         text=True, env={"PATH": str(tools), "AGENTLTL_CC_VENV": str(tmp_path / "venv")})
    assert out.returncode == 1
    assert "needs Python 3.10" in out.stderr
    assert not (tmp_path / "venv").exists()
