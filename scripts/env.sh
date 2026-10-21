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
    # agentltl-agentltl: plugin `agentltl` from marketplace `agentltl`; before it was renamed
    # the plugin was `agentltl-claude-code`.
    for d in "$HOME"/.claude/plugins/data/agentltl-agentltl \
             "$HOME"/.claude/plugins/data/agentltl-claude-code-agentltl; do
        if [[ -d "$d" ]]; then echo "$d"; return; fi
    done
}

env_migrate() {
    # Bring the session and project memory over from the plugin's name before the rename,
    # once, so `scope: project` rules keep their history.
    local old="$HOME/.claude/plugins/data/agentltl-claude-code-agentltl" data="${CLAUDE_PLUGIN_DATA:-}" d
    [[ -n "$data" && "$data" != "$old" && -d "$old" && ! -e "$data/.migrated" ]] || return 0
    mkdir -p "$data"
    for d in sessions projects; do
        if [[ -d "$old/$d" && ! -d "$data/$d" ]]; then cp -R "$old/$d" "$data/$d"; fi
    done
    touch "$data/.migrated"
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

env_venv_python() {
    # The interpreter inside virtualenv $1: bin/python on Linux and macOS, Scripts/python.exe
    # on Windows (Git Bash). Nothing, and a failure, when the virtualenv is not built.
    local p
    for p in "$1/bin/python" "$1/Scripts/python.exe" "$1/Scripts/python"; do
        if [[ -x "$p" ]]; then echo "$p"; return 0; fi
    done
    return 1
}

env_built() {
    # Virtualenv $1 has its interpreter and setup.sh ran to the end for the current pins (its
    # last step copies vendor.lock in). A run that failed half way leaves an interpreter but no
    # marker, and a pin change leaves a marker that no longer matches. One whose base
    # interpreter was upgraded or removed since (its python is a link to it) is not built either.
    cmp -s "$1/vendor.lock" "$root/vendor.lock" && env_venv_python "$1" >/dev/null && env_base_ok "$1"
}

env_smoke() {
    # Python $1 starts, in isolated mode, and can import what the guard needs.
    "$1" -I -c 'import sys; sys.version_info >= (3, 10) or sys.exit(1); import yaml, agentltl_coding' \
        >/dev/null 2>&1
}

env_base_ok() {
    # The base interpreter setup.sh recorded in $1/.base is still there. A base that is newer
    # than the record was upgraded (3.12 -> 3.13 under the same name breaks a venv): the guard's
    # imports must still work, checked once, then the record is refreshed. A venv without a
    # record (built before this check) passes.
    local base
    base="$(head -n1 "$1/.base" 2>/dev/null | tr -d '\r')"
    [[ -n "$base" && -e "$1/bin/python" ]] || return 0
    [[ -x "$base" ]] || return 1
    if [[ "$base" -nt "$1/.base" ]]; then
        env_smoke "$1/bin/python" || return 1
        touch "$1/.base"
    fi
    return 0
}

env_find_python() {
    # A Python 3.10+ to build the virtualenv with: $PYTHON, python3, python, then the Windows
    # `py` launcher. Windows has no python3 or only the Microsoft Store stub, which fails to run.
    local c exe ok='import sys; sys.exit(sys.version_info < (3, 10))'
    for c in "${PYTHON:-}" python3 python; do
        [[ -n "$c" ]] && command -v "$c" >/dev/null 2>&1 || continue
        if "$c" -c "$ok" >/dev/null 2>&1; then echo "$c"; return 0; fi
    done
    if command -v py >/dev/null 2>&1; then
        exe="$(py -3 -c 'import sys; print(sys.executable)' 2>/dev/null | tr -d '\r')"
        if [[ -n "$exe" ]] && "$exe" -c "$ok" >/dev/null 2>&1; then echo "$exe"; return 0; fi
    fi
    return 1
}

env_python() {
    # The Python to run the guard with, or nothing when it is not built yet.
    local c
    if [[ -n "${AGENTLTL_CC_PYTHON:-}" && -x "$AGENTLTL_CC_PYTHON" ]]; then
        echo "$AGENTLTL_CC_PYTHON"; return 0
    fi
    for c in "$root/.venv" "$(env_venv)"; do
        env_built "$c" && env_venv_python "$c" && return 0
    done
    return 1
}

env_build() {
    # Build the virtualenv for these pins unless it exists; one build at a time, since
    # parallel tool calls each run a hook. mkdir is an atomic lock that also works on macOS.
    # A lock whose builder died is taken over at once; a build that failed is not tried again
    # by every hook for 10 minutes (no network, no git): see env_doctor.
    local venv lock failed holder waited=0 status=0
    venv="$(env_venv)"
    lock="$(dirname "$venv")/.setup.lock"
    failed="$(dirname "$venv")/.build-failed"
    mkdir -p "$(dirname "$lock")"
    if [[ -n "$(find "$failed" -mmin -10 2>/dev/null)" ]]; then
        echo "AgentLTL: setup failed less than 10 minutes ago, not trying again yet (run $root/scripts/setup.sh to see why, or remove $failed)." >&2
        return 1
    fi
    while ! mkdir "$lock" 2>/dev/null; do
        holder="$(cat "$lock/pid" 2>/dev/null)"
        if [[ -n "$holder" ]] && ! kill -0 "$holder" 2>/dev/null; then
            rm -rf "$lock"; continue
        fi
        sleep 1; waited=$((waited + 1))
        if (( waited > 90 )); then rm -rf "$lock"; fi   # a crashed build left it behind
    done
    [[ -n "${BASHPID:-}" ]] && echo "$BASHPID" > "$lock/pid"
    if ! env_built "$venv"; then
        AGENTLTL_CC_VENV="$venv" "$root/scripts/setup.sh" >&2 || status=$?
    fi
    rm -rf "$lock"
    if (( status )); then touch "$failed"; else rm -f "$failed"; fi
    return $status
}

env_touch() {
    # Mark this environment as in use on every hook: another session's pruning keeps it.
    local venv
    venv="$(env_venv)"
    [[ -f "$venv/vendor.lock" ]] && touch "$venv/vendor.lock"
    return 0
}

env_heal() {
    # Python $1 just failed to run the guard. When it cannot even start and import the guard's
    # libraries, its virtualenv is broken (base interpreter upgraded or removed, files lost):
    # drop the marker, so the next env_build makes it again, and return 0. Return 1 when Python
    # is fine (the failure is the guard's own) or is not ours to rebuild (a test or dev override).
    [[ -z "${AGENTLTL_CC_PYTHON:-}" ]] || return 1
    env_smoke "$1" && return 1
    local venv
    venv="$(env_venv)"
    [[ "$1" == "$(env_venv_python "$venv")" ]] || return 1
    rm -f "$venv/vendor.lock"
}

env_doctor() {
    # What `agentltl doctor` prints. Bash, so it works when Python itself does not.
    local venv py data lock failed ok=0 out
    venv="$(env_venv)"; py="$(env_python)"; data="$(dirname "$venv")"
    lock="$data/.setup.lock"; failed="$data/.build-failed"
    echo "AgentLTL environment"
    echo "  plugin:        $root"
    echo "  virtualenv:    $venv ($(env_built "$venv" && echo built || echo "not built, or broken"))"
    echo "  python:        ${py:-none}"
    if [[ -n "$py" ]]; then
        echo "  resolves to:   $(readlink -f "$py" 2>/dev/null || echo "$py")"
        grep -E '^(home|version) ' "$venv/pyvenv.cfg" 2>/dev/null | sed 's/^/  pyvenv.cfg:    /'
        if out="$("$py" -I -c 'import sys; sys.version_info >= (3, 10) or sys.exit("Python 3.10 or newer is needed"); import yaml, agentltl_coding' 2>&1)"; then
            echo "  imports:       ok ($("$py" -I -c 'import sys; print(sys.version.split()[0])' 2>&1))"; ok=1
        else
            echo "  imports:       FAILED"; echo "$out" | sed 's/^/                 /'
        fi
    fi
    echo "  system python: $(env_find_python 2>/dev/null || echo "none found (3.10+)")"
    [[ -d "$lock" ]] && echo "  setup lock:    held by pid $(cat "$lock/pid" 2>/dev/null || echo unknown) (older than 90 s: removed automatically)"
    [[ -f "$failed" ]] && echo "  last setup:    failed $(date -r "$failed" 2>/dev/null); not retried for 10 minutes ($failed)"
    env | grep -E '^(PYTHON[A-Z]*|VIRTUAL_ENV|PYENV_VERSION|CONDA_PREFIX)=' | sed 's/^/  env:           /'
    if (( ok )); then echo "  verdict:       working"; return 0; fi
    echo "  verdict:       broken. To rebuild: rm -rf \"$venv\" \"$failed\" && $root/scripts/setup.sh"
    return 1
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
