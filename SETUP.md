# Setting up AgentLTL for Claude Code

These instructions are for Claude Code. A user pasted a prompt asking you to set up this plugin.
Follow the steps in order. Say briefly what you are doing at each step, and stop to ask the user
only where a step says so.

The plugin checks every tool call against the rules in an `AGENTLTL.yaml` file, and refuses
calls that break them. Nothing has to be cloned. The plugin installs from GitHub into
`~/.claude/plugins/` and works in every project. The only file that lives in a project is
`AGENTLTL.yaml`, at the project root.

## 1. Check the prerequisites

Run `python3 --version` and `git --version`.

- **Python 3.10 or newer and git:** both are needed. If either is missing or Python is
  older, stop and tell the user what to install. On macOS that is `brew install python git`;
  on Debian or Ubuntu, `sudo apt install python3 python3-venv git`.
- **`python3 -m venv`:** run `python3 -m venv --help > /dev/null`. On Debian and Ubuntu it can
  be missing even when Python is installed; the fix is `sudo apt install python3-venv`.

## 2. Look for an earlier setup

- **Already installed:** run `claude plugin list`. If `agentltl-claude-code@agentltl` is
  listed, skip to step 4.
- **Hooks from a manual setup:** read `~/.claude/settings.json`. If its `hooks` contain
  commands ending in `hooks/run PreToolUse` (or `SessionStart`, `PostToolUse`) that point at an
  `agentltl-claude-code` or `agentltl-guard` folder, the guard was set up by hand from a
  clone. Tell the user. Running both the plugin and those hooks records every call twice, so
  ask whether to remove those three hook entries. Remove only those entries, and keep
  everything else in the file.

## 3. Install the plugin

Run these in the shell. Use the HTTPS URL: the `owner/repo` short form clones over SSH, which
fails on machines without GitHub SSH keys.

```bash
claude plugin marketplace add https://github.com/lailanelkoussy/agentltl-claude-code.git
claude plugin install agentltl-claude-code@agentltl
```

If the first command says the marketplace already exists, run
`claude plugin marketplace update agentltl` instead and continue.

## 4. Turn on automatic updates

The `marketplace add` command wrote an entry for `agentltl` under `extraKnownMarketplaces` in
`~/.claude/settings.json`. Read the file, add `"autoUpdate": true` to that entry, and write it
back without changing anything else. The entry should end up like this:

```json
"extraKnownMarketplaces": {
  "agentltl": {
    "source": {"source": "git", "url": "https://github.com/lailanelkoussy/agentltl-claude-code.git"},
    "autoUpdate": true
  }
}
```

If the entry is missing, add it exactly as above. Check that the file is still valid JSON:
`python3 -m json.tool ~/.claude/settings.json > /dev/null`. A broken settings file silently
disables all of the user's settings.

## 5. Prepare the plugin's Python environment

The plugin builds its environment by itself the first time it runs. Building it now means the
first rules can be tested before they go live, so run:

```bash
plugin=$(ls -d ~/.claude/plugins/cache/agentltl/agentltl-claude-code/*/ | tail -1)
CLAUDE_PLUGIN_DATA=~/.claude/plugins/data/agentltl-claude-code-agentltl "$plugin/scripts/setup.sh"
```

It takes about 10 to 30 seconds and ends with `agentltl-claude-code installed in ...`. From now
on, run the plugin's command line as `"$plugin/bin/agentltl"` (re-set `plugin` the same way if
your shell forgot it). Once the plugin is loaded, it is on the PATH as plain `agentltl`.

## 6. Choose the first rules, with the user

The plugin ships a library of tested rules the user can switch on by name. Start there:
read `$plugin/skills/agentltl-setup/SKILL.md` and follow it. Until the plugin is loaded,
`agentltl` is not on the PATH, so write `"$plugin/bin/agentltl"` wherever that file says
`agentltl`. Run the commands from the project root.

Then ask whether there is anything else Claude should never do, or always do first, in this
project. For each answer, write a rule following `$plugin/skills/agentltl-rules/SKILL.md`. The
rule format is in `$plugin/skills/agentltl-rules/reference.md`. In short:

- **Tool names:** rules use the names commands translate to. Check with
  `"$plugin/bin/agentltl" translate "git push --force"`.
- **Explain each rule:** give every rule a `why` (Claude sees it when blocked) and a `mode`
  matching how strict the user wants it: `block`, `warn`, `ask`, `retry`, `stop` or `log`.
- **Test before saving:** check each rule against commands it should and should not stop:
  `"$plugin/bin/agentltl" check --add draft.yaml "deny: git push --force" "allow: git push"`.
  Fix it until every line ends in `ok` and there is no `WARNING`.

Show the user the final `"$plugin/bin/agentltl" validate` output.

## 7. Finish

Tell the user:

1. **Activate it:** run `/reload-plugins`, or restart Claude Code. The plugin is active from
   then on, in every project that has an `AGENTLTL.yaml`.
2. **Try it:** ask Claude to do something a rule forbids, and watch it get refused with the
   rule's reason.
3. **Browse the library again:** `/agentltl-claude-code:agentltl-setup`.
4. **Add or remove rules in plain words:** `/agentltl-claude-code:agentltl-rules never touch the
   lockfile`, or "remove the rule about force-pushing", or just ask Claude.
5. **See what it is doing:** `/agentltl-claude-code:agentltl` shows the rules in force and
   what the guard recently blocked.

## Troubleshooting

- **`Plugin not found in marketplace`:** run `claude plugin marketplace update agentltl`, then
  install again.
- **`Failed to clone` / `access rights`:** use the HTTPS URL from step 3, not `owner/repo`.
- **Setup fails with `ensurepip` or `No module named venv`:** install `python3-venv` (step 1).
- **Every call asks for permission with "AgentLTL ... is not installed":** the environment could
  not be built. Run step 5 and read its error.
- **A rule never fires:** run `"$plugin/bin/agentltl" validate` and fix any `WARNING`. It means
  a tool or argument name that no command produces.
