# AgentLTL for Claude Code

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Claude Code plugin](https://img.shields.io/badge/Claude%20Code-plugin-d97757.svg)](https://docs.claude.com/en/docs/claude-code)
[![arXiv](https://img.shields.io/badge/arXiv-2607.02599-b31b1b.svg)](https://arxiv.org/abs/2607.02599)

> Rules Claude Code can't forget.

`CLAUDE.md` is advice: Claude can lose it after compaction or inside a subagent. AgentLTL
checks every tool call against the rules in `AGENTLTL.yaml` **before it runs**, and refuses
the ones that break them.

```
> commit and push

  Bash  git add app.py && git commit -m "Fix login" && git push
  ✗ Rule 'tests-before-push' blocked this call. Nothing was executed.
    Problem: git_push needs pytest to have run first since the last edit.

  Bash  pytest -q                                               ✓
  Bash  git add app.py && git commit -m "Fix login" && git push  ✓
```

## Features

- **Order-aware rules:** enforce sequences, not just single calls: tests before push, plan before
  apply, staging before prod. Per session or across the whole project.
- **Real shell parsing:** `git commit -am x && git push -f` is checked as `git_commit` then
  `git_push{force: true}`, all or nothing.
- **Plain-language rules:** ask Claude to "add a rule: never push to main"; it writes the rule
  and tests it before saving.
- **Opt-in rule library:** 11 tested best-practice rules for git, secrets, installs and
  infrastructure. Activate the ones you want, skip the rest.
- **Rules instead of memory:** when Claude would save a rule to `CLAUDE.md` or its memory,
  it writes an enforced rule in `AGENTLTL.yaml` instead.
- **Fail-safe:** never approves a call; internal errors become a permission prompt.

## Installation

Requires Python 3.10+ and git. Paste this into Claude Code:

```
Set up the AgentLTL plugin for me by following https://raw.githubusercontent.com/lailanelkoussy/agentltl-claude-code/main/SETUP.md
```

Or install manually:

```bash
claude plugin marketplace add https://github.com/lailanelkoussy/agentltl-claude-code.git
claude plugin install agentltl@agentltl
```

Then enable auto-update (`/plugin` → **Marketplaces** → `agentltl`) and run `/reload-plugins`.
The plugin does nothing until there is an `AGENTLTL.yaml`: at a project's root for that
project, or in `~/.claude/` for every project.

**Updating:** with auto-update on, new versions arrive in the background. To update now, run
`/plugin update agentltl@agentltl`, then `/reload-plugins`.

## Usage

### Rule library

The plugin ships a library of best-practice rules. **Nothing is on by default**: you choose which
ones to activate, per project or for all projects, and leave the rest off.

| Rule | Effect |
|---|---|
| `tests-before-push` | Tests must run after the last edit, before pushing (pytest, npm/cargo/go/make test, tox, jest) |
| `no-force-push` | No force push (`--force-with-lease` is allowed) |
| `no-push-to-main` | No push to `main` / `master` by name |
| `ask-before-discarding-work` | Ask before `reset --hard`, `clean -f`, `branch -D`, `stash drop` |
| `ask-before-recursive-delete` | Ask before `rm -r` / `rm -rf` |
| `ask-before-installing` | Ask before installing packages (pip, npm, uv, cargo, apt, brew, ...) |
| `ask-before-infra-changes` | Ask before `terraform apply/destroy`, `kubectl apply/delete`, `aws s3 rm`, ... |
| `protect-env-files` | Never read or touch `.env` (`.env.example` is fine) |
| `read-before-overwrite` | Read a file before overwriting or editing it in place |
| `no-claude-coauthor` | Claude never signs commits, tags or notes |
| `subagents-on-sonnet` | Subagents run on Sonnet or Haiku, not a bigger model |

**Activate** them interactively with `/agentltl:setup`, or by name in `AGENTLTL.yaml`:

```yaml
use:
  - tests-before-push
  - no-force-push
  - {subagents-on-sonnet: {mode: warn}}   # same rule, softer mode
```

**Customise or opt out:**

- Change a rule's `mode` or `scope` inline, as above.
- Replace a library rule by writing your own with the same `id`.
- Switch off a single rule (including ones from `~/.claude/AGENTLTL.yaml`) with `disable: [id]`.
- From the shell: `agentltl library` lists entries, `agentltl use` / `unuse` toggle them
  (`--user` for all projects).

Library rules are referenced by name, not copied, so they improve with each plugin update.
Every entry has a behaviour test in the repo.

### Write your own

Ask Claude in plain words, or run `/agentltl:rules <rule>`. Or edit the YAML:

```yaml
rules:
  - id: tests-before-push
    before: {first: pytest, then: git_push, since: [Edit, Write]}
    why: Never push untested code.

  - id: one-pr-per-session
    at_most: {call: gh_pr_create, times: 1}
    mode: ask

  - id: staging-before-prod
    before:
      first: {tool: make, with: {argv: deploy-staging}}
      then: {tool: make, with: {argv: deploy-prod}}
    scope: project              # remembered across sessions
```

Rules for every project go in `~/.claude/AGENTLTL.yaml`.

### Rules instead of memory

When you tell Claude "remember: never push to main", it would normally write that to
`CLAUDE.md` or its auto memory, where it can be forgotten. The built-in `memory-first` rule
redirects it: Claude is steered to turn anything about tool calls into an enforced rule in
`AGENTLTL.yaml`, and to keep memory for facts, preferences and style.

It works by refusing each write to a memory file once, with a reminder. If the content can't
be a rule, Claude repeats the call and it goes through. Memory you edit yourself is never
checked. This rule is on by default; turn it off with `disable: [memory-first]`.

### Modes

| `mode` | On violation |
|---|---|
| `block` (default) | Refused every time |
| `warn` | Refused once; Claude may repeat the exact call to override |
| `ask` | You decide |
| `retry` | Refused; after 3 tries you're asked |
| `stop` | Refused, and Claude stops |
| `log` | Allowed; Claude is told it broke the rule |

### Commands

| Command | Description |
|---|---|
| `/agentltl:setup` | Pick rules from the library |
| `/agentltl:rules <rule>` | Add, change or remove a rule in plain words |
| `/agentltl:status` | Show active rules and recent blocks |

## How it works

Three Claude Code hooks: `SessionStart` lists the rules to Claude (again after compaction),
`PreToolUse` checks each call against the rules and the call history, and `PostToolUse` records
calls that ran. Shell commands are parsed by [cli-to-tools](https://github.com/lailanelkoussy/cli-to-tools);
rules are evaluated by [AgentLTL](https://github.com/lailanelkoussy/AgentLTL) (linear temporal logic).

## Limitations

The guard sees commands, not what programs do inside. It sees `make test`, not the `pytest` in
your Makefile, so list wrappers in your rules. It's a rulebook for Claude, not a sandbox.

Some shell commands can't be analysed: `eval`, a command named by a variable (`$CMD args`),
and background jobs (`cmd &`). By default, you're asked about them in normal mode. In auto
mode they go through, and Claude is told they weren't checked. `settings.unparseable` changes
both.

## Documentation

- [docs/REFERENCE.md](docs/REFERENCE.md): full reference (rule kinds, scopes, CLI, development)
- [examples/showcase.yaml](examples/showcase.yaml): deploys, git, infrastructure
- [examples/creative.yaml](examples/creative.yaml): test-first, research hygiene, prompt-injection tripwires

## Research

This plugin is part of a broader research effort around [AgentLTL](https://github.com/lailanelkoussy/AgentLTL),
a language derived from first-order linear temporal logic for expressing procedural rules over
agent traces. The same specification can score completed traces, gate tool calls before they
run (what this plugin does), or serve as a reward for fine-tuning.

> **AgentLTL: A Trace-Verification Framework for Measuring, Enforcing, and Training Procedural
> Compliance in Tool-Using LLM Agents**  
> Laïla Elkoussy, Julien Perez. [arXiv:2607.02599](https://arxiv.org/abs/2607.02599), 2026.

```bibtex
@misc{elkoussy2026agentltl,
  title         = {AgentLTL: A Trace-Verification Framework for Measuring, Enforcing, and Training Procedural Compliance in Tool-Using LLM Agents},
  author        = {Elkoussy, La{\"\i}la and Perez, Julien},
  year          = {2026},
  eprint        = {2607.02599},
  archivePrefix = {arXiv},
  primaryClass  = {cs.SE},
  url           = {https://arxiv.org/abs/2607.02599}
}
```

## Author

Made and maintained by **Laïla Elkoussy**. Questions, feedback or collaboration ideas:
[laila.elkoussy@epita.fr](mailto:laila.elkoussy@epita.fr).

## License

[MIT](LICENSE)
