"""Credentials in a call's output are reported by kind, never by value."""

import pytest

from agentltl_cc import hook
from agentltl_coding.scan import credential_kinds

# Built at run time so that no credential-shaped literal sits in the repository.
FAKE = {
    "AWS access key ID": "AKIA" + "ABCDEFGHIJ234567",
    "GitHub token": "ghp" + "_" + "a1B2c3D4e5" * 4,
    "private key": "-----BEGIN " + "OPENSSH PRIVATE KEY-----",
    "Slack token": "xox" + "b-" + "1234567890-abcdef",
}


@pytest.mark.parametrize("kind", sorted(FAKE))
def test_known_formats_are_recognised(kind):
    assert credential_kinds(f"config loaded: {FAKE[kind]} (ok)") == [kind]


def test_ordinary_output_is_quiet():
    assert credential_kinds("AKIA is a prefix; ghp_ too; the key is in the vault") == []


def test_structured_responses_are_scanned():
    assert credential_kinds({"stdout": "x", "stderr": FAKE["AWS access key ID"]}) == [
        "AWS access key ID"]


def _post(tmp_path, response, settings=""):
    (tmp_path / ".git").mkdir(exist_ok=True)
    (tmp_path / "AGENTLTL.yaml").write_text(settings + "rules: []\n")
    return hook.run({"hook_event_name": "PostToolUse", "session_id": "s", "cwd": str(tmp_path),
                     "tool_name": "Bash", "tool_input": {"command": "python app.py"},
                     "tool_response": response})


def test_the_hook_warns_without_repeating_the_value(tmp_path):
    out = _post(tmp_path, {"stdout": "token=" + FAKE["GitHub token"]})
    assert "GitHub token" in out["systemMessage"]
    context = out["hookSpecificOutput"]["additionalContext"]
    assert "Do not repeat" in context
    assert FAKE["GitHub token"] not in out["systemMessage"] + context


def test_scanning_can_be_turned_off(tmp_path):
    assert _post(tmp_path, FAKE["private key"], "settings: {scan_output: false}\n") is None
    assert _post(tmp_path, "all good") is None
