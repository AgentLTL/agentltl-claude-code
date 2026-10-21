#!/usr/bin/env bash
# Create the plugin's virtualenv with its dependencies: agentltl, cli-to-tools, pyyaml.
#
#   scripts/setup.sh            # venv in $CLAUDE_PLUGIN_DATA/venv-<pins checksum>, else <repo>/.venv
#   scripts/setup.sh --dev      # also this package (editable), pytest and ruff
#
# The guard's own code is not installed: the hooks run it from the plugin directory, so a
# plugin update (a new directory) takes effect without reinstalling. A new venv is only
# needed when vendor.lock changes; scripts/env.sh names it after the pins.
#
# Dependencies come from the vendor/ submodules when they are checked out, otherwise from
# GitHub at the commits pinned in vendor.lock.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=env.sh
source "$root/scripts/env.sh"
if [[ -n "${AGENTLTL_CC_VENV:-}" ]]; then
    venv="$AGENTLTL_CC_VENV"
elif [[ -n "${CLAUDE_PLUGIN_DATA:-}" ]]; then
    venv="$CLAUDE_PLUGIN_DATA/venv-$(cksum < "$root/vendor.lock" | cut -d' ' -f1)"
else
    venv="$root/.venv"
fi

# A virtualenv whose interpreter is gone or does not start (its base Python was upgraded or
# removed) is made again, not repaired: venv would keep a dangling link without --clear.
if ! env_venv_python "$venv" >/dev/null || ! "$(env_venv_python "$venv")" -c "import sys" >/dev/null 2>&1; then
    python="$(env_find_python)" || {
        echo "AgentLTL needs Python 3.10 or newer, and found none (tried \$PYTHON, python3, python, py -3)." >&2
        exit 1
    }
    "$python" -m venv --clear "$venv"
fi
export GIT_TERMINAL_PROMPT=0   # a git install must fail, not wait for a password nobody sees
# `python -m pip`, not bin/pip or Scripts/pip.exe: the layout differs between Linux/macOS and
# Windows, and on Windows pip.exe cannot upgrade itself.
vpy="$(env_venv_python "$venv")"
# An interrupted `python -m venv` leaves the interpreter before pip is bootstrapped.
"$vpy" -m pip --version >/dev/null 2>&1 || "$vpy" -m ensurepip --upgrade >/dev/null
# Not built, for any pins, until the last step: a failure part way must not leave the marker
# of an earlier run, which would make the half-changed environment look complete.
rm -f "$venv/vendor.lock"
pip=("$vpy" -m pip)
pipflags=(--disable-pip-version-check --no-input --timeout 30)
"${pip[@]}" install -q --upgrade pip "${pipflags[@]}"

# Editable installs only for development: a plugin install lives in a versioned directory
# that Claude Code deletes some days after an update, so it must get real copies.
editable=""
[[ "${1:-}" == "--dev" ]] && editable="-e"

while read -r name url commit; do
    [[ -z "$name" || "$name" == \#* ]] && continue
    case "$name" in agentltl) dir=AgentLTL ;; *) dir="$name" ;; esac
    if [[ -f "$root/vendor/$dir/pyproject.toml" ]]; then
        "${pip[@]}" install -q $editable "$root/vendor/$dir" "${pipflags[@]}"
    else
        "${pip[@]}" install -q "$name @ git+$url@$commit" "${pipflags[@]}"
    fi
done < "$root/vendor.lock"
"${pip[@]}" install -q "pyyaml>=6.0" "${pipflags[@]}"

if [[ "${1:-}" == "--dev" ]]; then
    # From the plugin directory with a relative path: Git Bash converts a bare /d/a/x path for
    # a Windows pip, but not one with an [extra] on the end.
    (cd "$root" && "${pip[@]}" install -q -e ".[dev]")
fi
# Complete only if the guard's libraries import in isolated mode, as the hooks run it.
env_smoke "$vpy" || {
    echo "AgentLTL: the new environment cannot import the guard's libraries:" >&2
    "$vpy" -I -c "import yaml, agentltl_coding" >&2 || true
    exit 1
}
# The base interpreter the venv's python links to, so a later upgrade or removal is noticed.
"$vpy" -I -c "import os, sys; print(os.path.realpath(sys._base_executable))" > "$venv/.base" 2>/dev/null || true
cp "$root/vendor.lock" "$venv/vendor.lock"
echo "AgentLTL installed in $venv"
