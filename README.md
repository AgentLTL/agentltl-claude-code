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
- **Variables across calls:** `$variables` tie calls together by their arguments: apply only
  the plan that was reviewed, promote only the exact version that ran in staging, delete only
  the files Claude wrote itself.
- **Real shell parsing:** `git commit -am x && git push -f` is checked as `git_commit` then
  `git_push{force: true}`, all or nothing.
- **Plain-language rules:** ask Claude to "add a rule: never push to main"; it writes the rule
  and tests it before saving.
- **Opt-in rule library:** 18 tested best-practice rules for git, secrets, installs and
  infrastructure, plus a `devops-secrets` bundle. Activate the ones you want, skip the rest.
- **Secret leak alerts:** when a command's output contains what looks like a credential, you
  are told which kind, so you can rotate it, and Claude is told not to repeat it.
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
| `read-before-overwrite` | Read a file before overwriting it (`Write`, `sed -i`, `>`; appending with `>>` is fine) |
| `no-claude-coauthor` | Claude never signs commits, tags or notes |
| `subagents-on-sonnet` | Subagents run on Sonnet or Haiku, not a bigger model |

**Secrets and DevOps.** Switch them all on with the `devops-secrets` bundle, or pick:

| Rule | Effect |
|---|---|
| `protect-secret-files` | Never read, copy, send or edit credential files: `.env`, keys and certificates, cloud and cluster credentials, tool tokens, shell history |
| `no-env-dumps` | Never print the environment or a secret-looking variable (`env`, `printenv`, `export -p`, `echo $TOKEN`, `docker inspect`, ...) |
| `ask-before-reading-secret-stores` | Ask before commands that print stored secrets: Kubernetes secrets, Vault, AWS/GCP/Azure secret managers, `terraform output`, password managers, `sops -d` |
| `ask-before-creating-credentials` | Ask before minting access keys, service-account keys, tokens or service principals |
| `no-secrets-in-commands` | Never put a secret on the command line: password flags, bearer headers, credentials in URLs |
| `no-leaky-debug` | Warn before debug output that prints credentials (`curl -v` with auth headers, `kubectl -v=6+`, `--debug`) |
| `no-skipping-secret-scans` | Ask before `--no-verify` (skips secret-scanning hooks) or `git add -f` |

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

### Supported commands

Rules refer to commands by the names they translate to, with their flags as named arguments.
For example, `kubectl delete pod x -n prod` becomes `kubectl_delete{namespace: prod, ...}`.
These commands are understood out of the box:

| Area | Commands |
|---|---|
| Shell and files | coreutils and text tools (`ls`, `cat`, `cp`, `mv`, `rm`, `find`, `sed`, `awk`, `grep`, ...), `printenv`/`export`/`declare`, `base64`/`strings`/`xxd`, editors and writers (`vim`, `nano`, `perl`, `dd`, `install`, `tee`, `sponge`, `patch`), archives (`tar`, `zip`, `unzip`, `7z`) |
| Git and GitHub | `git` (including `credential`), `gh` (including `pr`, `auth`, `secret`, `gist`) |
| Containers and clusters | `docker`, `docker compose`, `kubectl`, `helm` |
| Infrastructure and cloud | `terraform`, `aws` (`s3`, `secretsmanager`, `ssm`, `iam`, `sts`, `kms`, `ecr`, `configure`), `gcloud`, `az` |
| Secrets | `vault`, `sops`, `ansible-vault`, `gpg`, `age`, `openssl`, `op`, `bw`, `pass`, `security`, `secret-tool`, `doppler`, `heroku`, `vercel` |
| Languages and network | `python`, `pip`, `pytest`, `curl`, `wget`, `ssh`, `scp`, `rsync`, database clients (`mysql`, `psql`, `redis-cli`) |

Wrappers are unwrapped (`sudo`, `env`, `xargs`, `bash -c`, `find -exec`). Any other command is
still checked, under its own name with its words as `argv`. Claude can teach the plugin a new
command by adding a short spec under `tools:` in `AGENTLTL.yaml` (see the
[reference](docs/REFERENCE.md)).

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

### Rules that connect calls

A per-call checker can say "never run `terraform apply`". It can't say "apply only the plan
that was reviewed", because that depends on an earlier call and on what its arguments were.
AgentLTL can. A `$variable` in a `before` rule must take the same value in both calls:

```yaml
rules:
  # Production only gets a chart version that already ran in staging, even last week.
  - id: promote-what-staging-ran
    before:
      first: {tool: helm_upgrade, with: {chart: $chart, version: $v, namespace: staging}}
      then:  {tool: helm_upgrade, with: {chart: $chart, version: $v, namespace: prod}}
    scope: project

  # Apply exactly the plan that was shown, never a fresh one computed on the spot.
  - id: apply-the-reviewed-plan
    before:
      first: {tool: terraform_plan, with: {out: $plan}}
      then:  {tool: terraform_apply, with: {plan: $plan}}
  - id: apply-a-saved-plan
    require: {tool: terraform_apply, where: {plan: "?*"}}

  # Claude may clean up the files it wrote, and nothing else.
  - id: delete-only-what-you-wrote
    before:
      first: {tool: Write, with: {file_path: $f}}
      then:  {tool: rm, with: {paths: $f}}

  # Never run a SQL file that has not been read first.
  - id: read-sql-before-running-it
    before:
      first: {tool: Read, with: {file_path: $f}}
      then:  {tool: psql, with: {file: $f}}
```

What that looks like in a session:

```
  Bash  helm upgrade web repo/web --version 1.6.0 -n prod
  ✗ Rule 'promote-what-staging-ran' blocked this call. Nothing was executed.
    Problem: for chart='repo/web', v='1.6.0': no earlier helm_upgrade call had
             namespace='staging', chart='repo/web', version='1.6.0'.

  Bash  helm upgrade web repo/web --version 1.6.0 -n staging   ✓
  Bash  helm upgrade web repo/web --version 1.6.0 -n prod      ✓

  Bash  rm scratch.txt README.md
  ✗ Rule 'delete-only-what-you-wrote' blocked this call. Nothing was executed.
    Problem: for f='/repo/README.md': no earlier Write call had file_path='/repo/README.md'.
```

The checks work the way you'd want:
- **Every value is checked:** `rm a b` needs both files to have been written.
- **Variables combine:** a different chart with the same version doesn't count.
- **Paths are compared in one form:** `Read migrations/007.sql` and `psql -f ./migrations/007.sql`
  name the same file.

Under the hood, each rule is a first-order temporal formula, for example: for all chart `c`
and version `v`, every `helm_upgrade(c, v, prod)` comes after a `helm_upgrade(c, v, staging)`.

### Rules instead of memory

When you tell Claude "remember: never push to main", it would normally write that to
`CLAUDE.md` or its auto memory, where it can be forgotten. The built-in `memory-first` rule
redirects it: Claude is steered to turn anything about tool calls into an enforced rule in
`AGENTLTL.yaml`, and to keep memory for facts, preferences and style.

It works by refusing each write to a memory file once, with a reminder. If the content can't
be a rule, Claude repeats the call and it goes through. Memory you edit yourself is never
checked. This rule is on by default; turn it off with `disable: [memory-first]`.

### Secret leak alerts

A rule can only stop a call before it runs. When a program prints a credential anyway (an app
logging its config, a test dumping the environment), the value is already in the conversation.
After every call, the plugin scans the output for well-known credential formats: cloud access
key IDs, GitHub/GitLab/Slack/Stripe/npm tokens, Google, Anthropic and OpenAI API keys, private
keys. If it finds one:
- **you** see which kind of credential appeared, never the value, so you can rotate it;
- **Claude** is told not to repeat, copy or write it anywhere.

It is on by default; turn it off with `settings: {scan_output: false}`.

### Modes

| `mode` | On violation |
|---|---|
| `block` (default) | Refused every time |
| `warn` | Refused once; Claude may repeat the exact call to override |
| `ask` | You're asked to approve it; Claude can't override |
| `retry` | Refused twice; the third try asks you (`settings.retries`) |
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

Leak alerts recognise credential formats with a distinctive prefix. A plain password, or a
token without a known prefix, is not recognised.

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
