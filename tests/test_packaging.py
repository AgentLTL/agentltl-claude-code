"""vendor.lock must pin what the vendor/ submodules pin: it is what a marketplace install,
which has no submodules, installs from."""

import os
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _gitlinks():
    try:
        out = subprocess.run(["git", "ls-files", "-s", "vendor"], cwd=ROOT, check=True,
                             capture_output=True, text=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return {line.split()[3]: line.split()[1] for line in out.splitlines() if line.startswith("160000")}


def test_vendor_lock_matches_the_submodules():
    links = _gitlinks()
    pinned = {}
    with open(os.path.join(ROOT, "vendor.lock")) as fh:
        for line in fh:
            if line.strip() and not line.startswith("#"):
                name, _url, commit = line.split()
                pinned["vendor/" + ("AgentLTL" if name == "agentltl" else name)] = commit
    assert pinned == links
