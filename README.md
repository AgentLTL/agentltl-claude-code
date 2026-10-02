# AgentLTL for Claude Code

**Rules Claude Code can't forget.**

You can tell Claude "always run the tests before pushing" in `CLAUDE.md`, and most of the time
it listens. But in a long session, after the conversation gets compacted, or deep in a
subagent, it can forget. This plugin makes those rules stick. Before Claude runs anything, a
command, an edit or a web request, the plugin checks it against your rules. If the call breaks
one, it doesn't run, and Claude is told why.

```
> commit and push

  Bash  git add app.py && git commit -m "Fix login" && git push
  ✗ Rule 'tests-before-push' blocked this call. Nothing was executed.
    Problem: git_push needs pytest to have run first since the last edit.

  Bash  pytest -q                                              ✓
  Bash  git add app.py && git commit -m "Fix login" && git push ✓
```

## Set it up

Paste this into Claude Code:

```
Set up the AgentLTL plugin for me by following https://raw.githubusercontent.com/lailanelkoussy/agentltl-claude-code/main/SETUP.md
```

Claude will:
1. check that you have Python and git;
2. install the plugin and turn on automatic updates;
3. ask which rules you want, write them and test them.

Then run `/reload-plugins` (or restart Claude Code) and you're done.

There's nothing to clone. The plugin installs once for your user and works in every project
that has an `AGENTLTL.yaml` file at its root.

<details>
<summary>Prefer to do it by hand?</summary>

You need Python 3.10+ and git. In a terminal:

```bash
claude plugin marketplace add https://github.com/lailanelkoussy/agentltl-claude-code.git
claude plugin install agentltl-claude-code@agentltl
```

For automatic updates, open `/plugin` in Claude Code → **Marketplaces** → `agentltl` →
**Enable auto-update**. Then create an `AGENTLTL.yaml` at the root of your project (see below)
and run `/reload-plugins`.
</details>

## Picking ready-made rules

The plugin comes with a library of tested rules. Run `/agentltl-claude-code:setup`,
tick the ones you want, and you're done:

| Rule | What it does |
|---|---|
| `tests-before-push` | Run the tests after the last edit before pushing. |
| `no-force-push` | Never force-push (`--force-with-lease` is fine). |
| `no-push-to-main` | Never push to `main` or `master` directly. |
| `ask-before-discarding-work` | Ask before `reset --hard`, `clean -f`, `branch -D`, `stash drop`. |
| `no-claude-coauthor` | Claude never signs your commits. |
| `protect-env-files` | Never read or touch `.env` files. |
| `read-before-overwrite` | Read a file before overwriting it. |
| `ask-before-recursive-delete` | Ask before `rm -r`. |
| `ask-before-installing` | Ask before installing packages. |
| `ask-before-infra-changes` | Ask before `terraform apply`, `kubectl delete`, ... |
| `subagents-on-sonnet` | Subagents run on Sonnet, not on a bigger model. |

They are switched on by name, so they improve with each plugin update:

```yaml
use: [tests-before-push, no-force-push, {subagents-on-sonnet: {mode: warn}}]
```

## Adding and removing rules

Just ask Claude in plain words:

> Add a rule: never push to main.
>
> Add a rule: don't touch the .env file, ever.
>
> Add a rule: ask me before installing any package.
>
> Remove the rule that stops me from pushing to main.

Or use `/agentltl-claude-code:rules <your rule>`. Claude writes the rule, tests it
against examples it should and shouldn't catch, shows you the result, and saves it.

The rules live in `AGENTLTL.yaml`, which you can also edit yourself:

```yaml
rules:
  - id: tests-before-push
    before: {first: pytest, then: git_push, since: [Edit, Write]}
    why: Never push untested code.

  - id: no-secrets
    never: [Read, Edit, Write, cat]
    where: {"*": "**/.env"}
    why: .env holds passwords.
    mode: stop

  - id: ask-before-installing
    never: [pip_install, {tool: npm, with: {argv: install}}]
    mode: ask
```

Put rules you want in every project in `~/.claude/AGENTLTL.yaml`.

## What happens when Claude breaks a rule

Each rule has a `mode`; pick how strict it should be:

| mode | what happens |
|---|---|
| `block` (default) | Refused every time. |
| `warn` | Refused once, with the reason. Claude can insist by trying the exact same thing again. |
| `ask` | You get asked, and you decide. |
| `retry` | Refused, and after 3 tries you're asked. |
| `stop` | Refused, and Claude stops working. |
| `log` | Allowed, but Claude is told it broke the rule. |

## Good to know

- **It checks commands, not what programs do inside.** It sees `make test`, not the `pytest`
  that the Makefile runs. When a rule mentions a tool, mention its wrappers too.
- **It can learn new commands.** It understands git, common shell tools, Python, Docker,
  kubectl, terraform, gh and aws out of the box. For another command, such as `helm`, Claude
  adds a short description of it to `AGENTLTL.yaml`, so rules can refer to its flags.
- **Rules go here, not in Claude's memory.** When you ask Claude to "remember" something
  like "never push to main", it is nudged to make it a rule instead of a `CLAUDE.md` line it
  could forget. Facts and preferences still go to memory.
- **It has two memories.** Rules remember what happened in the current session by default.
  A rule with `scope: project` remembers every session in the project ("staging was deployed
  yesterday").
- **Updates are automatic.** With auto-update on, new versions arrive in the background.
- **You can turn it off.** Delete `AGENTLTL.yaml` (it then does nothing), or run
  `/plugin` and disable `agentltl-claude-code`.
- **You can see what it's doing.** `/agentltl-claude-code:status` shows the rules in force and
  what it blocked recently.

## More

- [docs/REFERENCE.md](docs/REFERENCE.md): everything else, including rule kinds, variables,
  the two memories, the command-line tool, limits and development.
- [examples/](examples): ready-made rule sets: [showcase](examples/showcase.yaml) (deploys, git,
  networks) and [creative](examples/creative.yaml) (research hygiene, test-first, prompt-injection
  tripwires).
- [SETUP.md](SETUP.md): the setup steps Claude follows.

Built on [AgentLTL](https://github.com/lailanelkoussy/AgentLTL) (temporal-logic rules for
agents) and [cli-to-tools](https://github.com/lailanelkoussy/cli-to-tools) (turns shell
commands into structured tool calls).
