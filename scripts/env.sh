# Sourced by hooks/run, bin/agentltl and scripts/setup.sh: where the plugin's Python lives.
#
# Each set of dependency pins (vendor.lock) gets its own virtualenv, venv-<checksum>, in the
# plugin's data directory. A plugin update that moves a pin builds a new one on its first use,
# while a session still running the previous version keeps using the old one: the two never
# rebuild over each other. Environments unused for 14 days are removed.
#
# A checkout with its own .venv (scripts/setup.sh without a data directory, or --dev) uses
# that instead. Expects $root (the plugin directory).

env_data_dir() {
    if [[ -n "${CLAUDE_PLUGIN_DATA:-}" ]]; then echo "$CLAUDE_PLUGIN_DATA"; return; fi
    local d
    for d in "$HOME"/.claude/plugins/data/agentltl-claude-code*/; do
        if [[ -d "$d" ]]; then echo "${d%/}"; return; fi
    done
}

env_venv() {
    # The virtualenv for this plugin directory's pins, or <plugin>/.venv without a data dir.
    if [[ -n "${AGENTLTL_CC_VENV:-}" ]]; then echo "$AGENTLTL_CC_VENV"; return; fi
    local data sum
    data="$(env_data_dir)"
    if [[ -z "$data" ]]; then echo "$root/.venv"; return; fi
    sum="$(cksum < "$root/vendor.lock" | cut -d' ' -f1)"
    echo "$data/venv-$sum"
}

env_python() {
    # The Python to run the guard with, or nothing when it is not built yet.
    local c
    for c in "${AGENTLTL_CC_PYTHON:-}" "$root/.venv/bin/python" "$(env_venv)/bin/python"; do
        if [[ -n "$c" && -x "$c" ]]; then echo "$c"; return 0; fi
    done
    return 1
}

env_build() {
    # Build the virtualenv for these pins unless it exists; one build at a time, since
    # parallel tool calls each run a hook. mkdir is an atomic lock that also works on macOS.
    local venv lock waited=0
    venv="$(env_venv)"
    lock="$(dirname "$venv")/.setup.lock"
    mkdir -p "$(dirname "$lock")"
    while ! mkdir "$lock" 2>/dev/null; do
        sleep 1; waited=$((waited + 1))
        if (( waited > 90 )); then rm -rf "$lock"; fi   # a crashed build left it behind
    done
    if [[ ! -x "$venv/bin/python" ]]; then
        AGENTLTL_CC_VENV="$venv" "$root/scripts/setup.sh" >&2
    fi
    rmdir "$lock" 2>/dev/null
}

env_touch_and_prune() {
    # Mark this environment as in use; remove the plugin's other environments in the data
    # directory (venv-*, and the older single venv) unused for 14 days.
    local data venv stamp
    data="$(env_data_dir)"
    [[ -z "$data" || -n "${AGENTLTL_CC_VENV:-}" ]] && return 0
    venv="$(env_venv)"
    [[ -f "$venv/vendor.lock" ]] && touch "$venv/vendor.lock"
    for stamp in "$data"/venv/vendor.lock "$data"/venv-*/vendor.lock; do
        [[ -f "$stamp" && "$(dirname "$stamp")" != "$venv" ]] || continue
        if [[ -n "$(find "$stamp" -mtime +14 2>/dev/null)" ]]; then rm -rf "$(dirname "$stamp")"; fi
    done
    return 0
}
