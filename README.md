# agentltl-guard

A Claude Code plugin that enforces project rules on every tool call with
[AgentLTL](https://github.com/lailanelkoussy/AgentLTL), shell commands included. Rules live in
`AGENTLTL.yaml` at the project root, next to `CLAUDE.md`. Claude can forget a rule written in
`CLAUDE.md`, for example after a compaction or deep into a long session. It cannot get a call
past the guard.

```yaml
# AGENTLTL.yaml
rules:
  - id: tests-before-push
    before: {first: pytest, then: git_push, since: [Edit, Write]}
    why: CI is slow; never push untested changes.
  - id: no-force-push
    never: git_push
    with: {force: true}
    mode: warn
```

```
> commit and push
  Bash  git add app.py && git commit -m "..." && git push origin main
  ✗ [AGENTLTL] Rule 'tests-before-push' blocked this call (block). Nothing was executed.
    Problem: git_push needs pytest to have run first since the last Edit or Write (call #2).
  Bash  python -m pytest -q        ✓
  Bash  git add app.py && git commit -m "..." && git push origin main   ✓
```

## How it works

| Hook | What it does |
|---|---|
| `SessionStart` | Lists the rules to Claude, including after compaction (`settings.announce: false` turns this off). Reports errors in the rule file. |
| `PreToolUse` | Translates the call and checks it against the session's trace. Breaking a rule gives deny, ask, or deny-and-stop, according to the rule's mode. Otherwise the hook says nothing, and Claude Code's normal permissions (your settings, auto mode) decide. |
| `PostToolUse` | Appends the call that ran to the session trace. Denied or refused calls never count. |

How calls are translated:

- `Bash` command lines go through
  [cli-to-tools](https://github.com/lailanelkoussy/cli-to-tools). `git commit -am x && git push -f`
  is checked as `git_commit{message, all}` then `git_push{force: true}`, all or nothing.
- Output redirections appear as `redirect_to`.
- Other tools keep their Claude Code name and input (`Edit{file_path, ...}`).

The trace is kept per session under the plugin's data directory, and survives compaction and
resume. The guard never approves a call. An internal error becomes a permission prompt rather
than a silent pass.

### Escalation modes (AgentLTL severities)

| `mode` | Severity | On a violation |
|---|---|---|
| `block` (default) | PERSISTENT_BLOCK | Denied every time. Claude cannot override. |
| `warn` | BLOCK_AND_WARN | Denied once. Claude may override by repeating the exact call. |
| `retry` | SOFT_BLOCK | Denied. After `settings.retries` refusals, you are asked. |
| `ask` | PERSISTENT_BLOCK | You get a permission prompt with the reason. |
| `stop` | HARD_STOP | Denied, and Claude stops. |
| `log` | TOLERATE | Allowed. Claude is told it broke the rule. |

When one call breaks several rules, the strongest mode decides. This also stops a `warn`
override from slipping past a `block` rule.

### Commands the guard cannot analyse

These are `eval`, `cmd &`, `$CMD args` and function definitions, which cli-to-tools rejects. What
happens to them is set by `settings.unparseable`:

- **Normal modes:** default `ask`. You get a prompt saying the command was not checked, and
  which rules could apply.
- **Auto, bypass and dontAsk modes:** default `note`. The command goes through, and Claude is
  told that it was not checked against the rules.

## Rules

The full reference is in
[`skills/agentltl-rules/reference.md`](skills/agentltl-rules/reference.md).

Rule kinds:

| Kind | Meaning |
|---|---|
| `never` | A call matching the target is never made. |
| `before` | A call needs an earlier call. Optional `since`: the earlier call must come after the last call matching `since`. |
| `require` | A call's arguments must match. |
| `at_most` | A cap on matching calls. |
| `ltl` / `formula` | Raw AgentLTL. Formulas AgentLTL classifies as unsafe to enforce (liveness) are rejected. |

Targets:

- name a tool, or a list of tools;
- narrow it with `with` (equal values) or `where` (globs on values; the key `"*"` means any
  argument);
- add `exists: true/false` to match only paths that already exist, or only new ones;
- give a list of targets to match any of them.

`tools:` takes cli-to-tools specs for project commands. `~/.claude/AGENTLTL.yaml` holds rules for
every project; a project rule with the same id replaces it.

### Writing rules in plain words

Ask Claude, or run `/agentltl-guard:agentltl-rules <your rule in words>`. The skill:

1. looks up the real tool names (`agentltl translate`, `agentltl tools`);
2. drafts the rule;
3. tests it against commands it should and should not catch (`agentltl check --add draft.yaml
   "deny: ..." "allow: ..."`);
4. shows you the result, then edits `AGENTLTL.yaml`.

## CLI

The plugin puts `agentltl` on Claude's PATH:

```
agentltl validate                   rules in force, with warnings for names nothing produces
agentltl check "deny: git push" "allow: pytest" 'Edit {"file_path": "a.lock"}'
agentltl translate "git push -f origin main"
agentltl tools 'git_*'
agentltl trace | reset              this session's trace and interventions
```

`/agentltl-guard:agentltl` shows the guard's status.

## Install

```bash
git clone --recurse-submodules https://github.com/lailanelkoussy/agentltl-guard
agentltl-guard/scripts/setup.sh          # venv with vendor/AgentLTL and vendor/cli-to-tools
claude --plugin-dir ./agentltl-guard     # or add it through a marketplace
```

`vendor/` pins AgentLTL and cli-to-tools as git submodules. Without them, `setup.sh` installs
both from GitHub. If the plugin is installed without a venv, the first `SessionStart` runs
`setup.sh` into `$CLAUDE_PLUGIN_DATA/venv`.

## Limits

- **Only the command is seen.** The guard does not see inside scripts or programs (`make test`,
  `bash x.sh`, `python -c`). It sees `make` with `argv: [test]`.
- **Paths are resolved approximately.** Relative paths are resolved against the session's
  working directory, not against a `cd` earlier in the same command line.
- **`exists` uses the disk as it is now.** It is checked when the call is made. Earlier calls in
  the trace are judged against the current disk.
- **One AgentLTL operator misbehaves.** `X(...)` passes AgentLTL's safety classifier, but
  checked call by call it refuses the very call it applies to. Prefer the rule kinds.

## Development

```bash
scripts/setup.sh --dev
.venv/bin/pytest
.venv/bin/ruff check src tests
```
