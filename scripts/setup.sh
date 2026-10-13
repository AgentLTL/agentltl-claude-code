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

if ! env_venv_python "$venv" >/dev/null; then
    python="$(env_find_python)" || {
        echo "AgentLTL needs Python 3.10 or newer, and found none (tried \$PYTHON, python3, python, py -3)." >&2
        exit 1
    }
    "$python" -m venv "$venv"
fi
# `python -m pip`, not bin/pip or Scripts/pip.exe: the layout differs between Linux/macOS and
# Windows, and on Windows pip.exe cannot upgrade itself.
pip=("$(env_venv_python "$venv")" -m pip)
"${pip[@]}" install -q --upgrade pip

# Editable installs only for development: a plugin install lives in a versioned directory
# that Claude Code deletes some days after an update, so it must get real copies.
editable=""
[[ "${1:-}" == "--dev" ]] && editable="-e"

while read -r name url commit; do
    [[ -z "$name" || "$name" == \#* ]] && continue
    case "$name" in agentltl) dir=AgentLTL ;; *) dir="$name" ;; esac
    if [[ -f "$root/vendor/$dir/pyproject.toml" ]]; then
        "${pip[@]}" install -q $editable "$root/vendor/$dir"
    else
        "${pip[@]}" install -q "$name @ git+$url@$commit"
    fi
done < "$root/vendor.lock"
"${pip[@]}" install -q "pyyaml>=6.0"

if [[ "${1:-}" == "--dev" ]]; then
    "${pip[@]}" install -q -e "$root[dev]"
fi
cp "$root/vendor.lock" "$venv/vendor.lock"
echo "AgentLTL installed in $venv"
