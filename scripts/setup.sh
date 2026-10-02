#!/usr/bin/env bash
# Create the plugin's virtualenv with its dependencies: agentltl, cli-to-tools, pyyaml.
#
#   scripts/setup.sh            # venv in $CLAUDE_PLUGIN_DATA/venv, else <repo>/.venv
#   scripts/setup.sh --dev      # also this package (editable), pytest and ruff
#
# The guard's own code is not installed: the hooks run it from the plugin directory, so a
# plugin update (a new directory) takes effect without reinstalling. The venv only needs
# rebuilding when vendor.lock changes; hooks/run does that at session start.
#
# Dependencies come from the vendor/ submodules when they are checked out, otherwise from
# GitHub at the commits pinned in vendor.lock.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
venv="${AGENTLTL_CC_VENV:-${CLAUDE_PLUGIN_DATA:+$CLAUDE_PLUGIN_DATA/venv}}"
venv="${venv:-$root/.venv}"

python="${PYTHON:-python3}"
[[ -x "$venv/bin/python" ]] || "$python" -m venv "$venv"
pip="$venv/bin/pip"
"$pip" install -q --upgrade pip

# Editable installs only for development: a plugin install lives in a versioned directory
# that Claude Code deletes some days after an update, so it must get real copies.
editable=""
[[ "${1:-}" == "--dev" ]] && editable="-e"

while read -r name url commit; do
    [[ -z "$name" || "$name" == \#* ]] && continue
    case "$name" in agentltl) dir=AgentLTL ;; *) dir="$name" ;; esac
    if [[ -f "$root/vendor/$dir/pyproject.toml" ]]; then
        "$pip" install -q $editable "$root/vendor/$dir"
    else
        "$pip" install -q "$name @ git+$url@$commit"
    fi
done < "$root/vendor.lock"
"$pip" install -q "pyyaml>=6.0"

if [[ "${1:-}" == "--dev" ]]; then
    "$pip" install -q -e "$root[dev]"
fi
cp "$root/vendor.lock" "$venv/vendor.lock"
echo "agentltl-claude-code installed in $venv"
