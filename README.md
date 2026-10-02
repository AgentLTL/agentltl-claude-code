# agentltl-claude-code (AgentLTL for Claude Code)

Project rules that Claude Code cannot forget. Write them in `AGENTLTL.yaml`, next to
`CLAUDE.md`, and every tool call Claude makes, shell commands included, is checked against
them with [AgentLTL](https://github.com/lailanelkoussy/AgentLTL) before it runs. Claude can lose
track of a rule written in `CLAUDE.md`, after a compaction, deep into a long session, or inside a
subagent. A call that breaks an `AGENTLTL.yaml` rule is refused, and Claude is told why.

## Setup

```bash
git clone --recurse-submodules https://github.com/lailanelkoussy/agentltl-claude-code
agentltl-claude-code/scripts/setup.sh      # venv with vendor/AgentLTL and vendor/cli-to-tools
```

Then either:

- **Load it as a plugin for one session:**
  `claude --plugin-dir ./agentltl-claude-code`. You can also add it through a marketplace.
  If the plugin starts without a venv, the first session start runs `setup.sh` into
  `$CLAUDE_PLUGIN_DATA/venv`.
- **Turn it on for every session:** point your user hooks at it in `~/.claude/settings.json`:

  ```json
  "hooks": {
    "SessionStart": [{"hooks": [{"type": "command", "command": "<repo>/hooks/run SessionStart", "timeout": 600}]}],
    "PreToolUse":   [{"matcher": "*", "hooks": [{"type": "command", "command": "<repo>/hooks/run PreToolUse"}]}],
    "PostToolUse":  [{"matcher": "*", "hooks": [{"type": "command", "command": "<repo>/hooks/run PostToolUse"}]}]
  }
  ```

With no `AGENTLTL.yaml` in the project or in `~/.claude`, the hooks exit at once and do nothing.

## Examples

```yaml
# AGENTLTL.yaml
rules:
  - id: tests-before-push
    before: {first: pytest, then: git_push, since: [Edit, Write]}
    why: CI is slow; never push untested changes.

  - id: no-force-push
    never: git_push
    with: {force: true}
    mode: warn                        # Claude may insist once, by repeating the exact call

  - id: no-secrets
    never: [Edit, Write, Read, cat, cp, mv, rm, sed, tee, echo]
    where: {"*": "**/.env"}           # any argument, including `> .env`
    mode: stop                        # refuse, and stop Claude

  - id: migrations-are-history
    never: {tool: [Edit, Write, rm, mv], where: {"*": "migrations/*"}, exists: true}
    why: Applied migrations must not change; add a new one instead.
    mode: ask                         # you decide

  - id: one-release
    at_most: {call: {tool: make, with: {argv: release}}, times: 1}
    scope: project                    # counts across every session in this project
```

What it looks like in a session:

```
> commit and push
  Bash  git add app.py && git commit -m "..." && git push origin main
  ✗ [AGENTLTL] Rule 'tests-before-push' blocked this call (block). Nothing was executed.
    Problem: git_push needs pytest to have run first since the last Edit or Write (call #2).
  Bash  python -m pytest -q        ✓
  Bash  git add app.py && git commit -m "..." && git push origin main   ✓
```

The whole command line was refused, `git add` and `git commit` included, because one part of it
broke the rule.

Rules you want everywhere go in `~/.claude/AGENTLTL.yaml`. A project rule with the same `id`
replaces the user-level one.

### Writing rules in plain words

Ask Claude ("add a rule that we never push to main"), or run
`/agentltl-claude-code:agentltl-rules <your rule in words>`. The skill:

1. looks up the real tool names (`agentltl translate`, `agentltl tools`);
2. drafts the rule;
3. tests the draft against commands it should and should not catch
   (`agentltl check --add draft.yaml "deny: ..." "allow: ..."`);
4. shows you the result, then edits `AGENTLTL.yaml`.

## How it works

| Hook | What it does |
|---|---|
| `SessionStart` | Lists the rules to Claude, including after compaction (`settings.announce: false` turns this off). Reports errors in the rule file. |
| `PreToolUse` | Translates the call and checks it against what has already run. Breaking a rule gives deny, ask, or deny-and-stop, depending on the rule's mode. Otherwise the hook says nothing, and Claude Code's normal permissions (your settings, auto mode) decide. |
| `PostToolUse` | Records the call that ran. Calls that were denied, or that you refused, never count. |

How calls are translated:

- **Shell commands:** `Bash` command lines go through
  [cli-to-tools](https://github.com/lailanelkoussy/cli-to-tools).
  - `git commit -am x && git push -f` is checked as `git_commit{message, all}` then
    `git_push{force: true}`, all or nothing.
  - Output redirections appear as `redirect_to`.
- **Other tools:** they keep their Claude Code name and input (`Edit{file_path, ...}`).

The guard never approves a call. An internal error becomes a permission prompt rather than a
silent pass.

### Memory: session and project

Each rule reads one of two memories, set by `scope:` (the default comes from `settings.scope`,
which defaults to `session`):

| `scope` | Remembers | Resets |
|---|---|---|
| `session` | The calls made in this Claude Code session. | With each new session. Compaction and resume keep it. |
| `project` | Every call made in this project, across all sessions. | Never on its own. `agentltl reset --project` clears it. |

Use `session` for "since you started working" rules: tests before pushing, at most one
migration per task. Use `project` for facts that stay true: a one-time setup step, a release
cap. Every call that runs is recorded in both memories, so a rule moved from one scope to the
other already has history.

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

The full reference, with worked examples, is in
[`skills/agentltl-rules/reference.md`](skills/agentltl-rules/reference.md).

| Kind | Meaning |
|---|---|
| `never` | A call matching the target is never made. |
| `before` | A call needs an earlier call. Optional `since`: the earlier call must come after the last call matching `since`. |
| `require` | A call's arguments must match. |
| `at_most` | A cap on matching calls. |
| `ltl` / `formula` | Raw AgentLTL. Use `now("x")` for "this call is x" and `called("x")` for "x happened at some point". Formulas AgentLTL classifies as unsafe to enforce (liveness) are rejected. |

Targets:

- name a tool, or a list of tools;
- narrow it with `with` (equal values) or `where` (globs on values; the key `"*"` means any
  argument);
- add `exists: true/false` to match only paths that already exist, or only new ones;
- give a list of targets to match any of them.

`tools:` takes cli-to-tools specs for project commands, so rules can name their arguments.

## CLI

The plugin puts `agentltl` on Claude's PATH:

```
agentltl validate                   rules in force, with warnings for names nothing produces
agentltl check "deny: git push" "allow: pytest" 'Edit {"file_path": "a.lock"}'
agentltl translate "git push -f origin main"
agentltl tools 'git_*'
agentltl trace                      what has been recorded, for this session and the project
agentltl reset [--project]          forget the session's (or the project's) memory
```

`/agentltl-claude-code:agentltl` shows the guard's status.

## Limits

### The guard sees commands, not what programs do

The guard reads the command line Claude sends, splits it into the commands it contains, and
checks those. It does not run anything, and it cannot look inside a program to see what that
program does once it starts. Anything that happens inside a program is invisible to it:

| Claude runs | The guard sees | It does not see |
|---|---|---|
| `make test` | `make` with `argv: [test]` | that the Makefile runs `pytest` |
| `bash cleanup.sh` | `bash` with `argv: [cleanup.sh]` | the `rm -rf build` inside the script |
| `npm run deploy` | `npm` with `argv: [run, deploy]` | the deploy script in `package.json` |
| `python -c "import shutil; shutil.rmtree('x')"` | `python` with `code: "import shutil; ..."` | that Python deletes a directory |
| `git commit` | `git_commit` | the pre-commit hook that runs |

This cuts both ways:

- **A rule can be broken without the guard noticing.** `never: rm` stops `rm -rf build`, but not
  `bash cleanup.sh` when the script contains that `rm`. A rule is a rule about the commands
  Claude types, not about effects on your machine. Use a sandbox or file permissions for those.
- **A rule can miss that it was satisfied.** `before: [pytest, git_push]` doesn't count
  `make test` as running the tests, so the push is still refused.
  - Fix it by naming the wrappers too:
    `first: [pytest, {tool: make, with: {argv: test}}, {tool: npm, with: {argv: test}}]`.
  - `agentltl translate "<command>"` shows how a command is seen.

### To do

- **Follow `cd` inside a command line.** Relative paths are resolved against the session's
  working directory. A `cd` earlier in the same command line is not followed, so
  `cd migrations && rm 001.sql` is checked as `<project>/001.sql`. A `cd` in an earlier,
  separate command does count, because Claude Code passes the new directory to the next call.
- **Judge `exists` against the disk as it was.** `exists` is checked on disk when the call is
  made, which is right for the call being checked. Rules that look back at earlier calls
  (`before`, `at_most`) re-judge those calls against the disk as it is now. A file created and
  then edited twice would count as "existing" for all three calls. The fix is to record, with
  each call, whether its paths existed when it ran.
- **Read what is outside the command line.** A commit message given with `-F msgfile` or written
  in the editor, environment variables such as `GIT_AUTHOR_NAME=...`, and `git config` changes
  made earlier are not checked.

## Development

```bash
scripts/setup.sh --dev
.venv/bin/pytest
.venv/bin/ruff check src tests
```
