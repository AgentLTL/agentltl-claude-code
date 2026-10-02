#!/usr/bin/env bash
# Create the plugin's virtualenv and install agentltl, cli-to-tools and this package into it.
#
#   scripts/setup.sh            # venv in $CLAUDE_PLUGIN_DATA/venv, else <repo>/.venv
#   scripts/setup.sh --dev      # also pytest and ruff
#
# The submodules under vendor/ are used when present; otherwise both are installed from GitHub.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
venv="${AGENTLTL_GUARD_VENV:-${CLAUDE_PLUGIN_DATA:+$CLAUDE_PLUGIN_DATA/venv}}"
venv="${venv:-$root/.venv}"
extra=""
[[ "${1:-}" == "--dev" ]] && extra="[dev]"

python="${PYTHON:-python3}"
"$python" -m venv "$venv"
pip="$venv/bin/pip"
"$pip" install -q --upgrade pip

if [[ -f "$root/vendor/AgentLTL/pyproject.toml" ]]; then
    "$pip" install -q -e "$root/vendor/AgentLTL"
else
    "$pip" install -q "agentltl @ git+https://github.com/lailanelkoussy/AgentLTL.git"
fi
if [[ -f "$root/vendor/cli-to-tools/pyproject.toml" ]]; then
    "$pip" install -q -e "$root/vendor/cli-to-tools"
else
    "$pip" install -q "cli-to-tools @ git+https://github.com/lailanelkoussy/cli-to-tools.git"
fi
"$pip" install -q -e "$root$extra"
echo "agentltl-guard installed in $venv"
