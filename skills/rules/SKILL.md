---
name: rules
description: Add, change or remove a rule, in plain words
when_to_use: The user asks to add, change, remove, switch off, explain or debug an AGENTLTL rule ("never force-push", "run the tests before pushing", "don't touch .env", "remove the rule about force-pushing", "stop blocking rm -rf"), or to translate a policy into AgentLTL / LTL constraints. Also use it instead of saving to memory (CLAUDE.md, auto memory) whenever what you would remember says which tool calls or commands to make, avoid, or make first ("remember to never...", "from now on always ... before ...").
argument-hint: "<rule, e.g. never push to main>"
---

# Writing AGENTLTL rules

The agentltl-claude-code plugin checks every tool call Claude makes against `AGENTLTL.yaml` at the
project root (and `~/.claude/AGENTLTL.yaml` for rules that apply everywhere). Shell commands are
parsed into structured calls first: `git commit -am x && git push -f` is checked as
`git_commit{message: x, all: true}` then `git_push{force: true}`.

The user's request: $ARGUMENTS

Rule kinds, target syntax, modes and worked examples are in
[reference.md](${CLAUDE_PLUGIN_ROOT}/skills/rules/reference.md). Read it before you
write a rule.

If the user wants to remove, switch off or loosen a rule, go to
[Removing or loosening a rule](#removing-or-loosening-a-rule).

## Steps

1. **Read the current rules**: run `agentltl validate`. If there is no `AGENTLTL.yaml` yet,
   you will create one at the project root.

   **Check the library first**: run `agentltl library`. If a packaged rule already does what
   the user asked, offer it instead of writing a new one. It is tested, and it gets fixes
   with plugin updates. Switch it on with `agentltl use NAME`, or with `--mode M` for another
   strictness, then validate and stop here. If it almost fits, write a project rule with the
   same id: it replaces the packaged one.

2. **Find the real tool names and arguments.** Rules refer to the names the translator produces,
   not to the words in the command.
   - Run `agentltl translate "<a command the rule is about>"` for one or two typical commands,
     for example `agentltl translate "git push --force origin main"`.
   - Run `agentltl tools 'git_*'` to list the tools and their arguments.
   - If the argument the rule needs is not named, add or extend a spec under `tools:`. That
     happens when the result is `{"argv": [...]}` (no spec), when the flag is in
     `extra_args`, or when a value landed in the wrong argument. See "When a command needs a
     spec" in reference.md. The spec applies to Claude's Bash calls too, not just to
     `translate`.
   - Claude Code's own tools keep their names (`Edit`, `Write`, `Read`, `WebFetch`, `mcp__...`)
     and their input fields (`file_path`).
   - Think about every way the action can be done. "Don't touch .env" covers `Edit`, `Write`,
     `Read`, and also `cat`, `cp`, `mv`, `rm`, `sed`, `tee`, and `echo ... > .env` (that last one
     is `redirect_to`). Name all of them, or use the `"*"` argument key.

3. **Draft the rule** in a scratch file, not in `AGENTLTL.yaml` yet.
   - Prefer a rule kind (`never`, `before`, `require`, `at_most`) over raw `ltl`/`formula`.
   - Always write `why` (Claude sees it when blocked) and, when there is a clear way to comply,
     `fix`.
   - Pick the `mode` from what the user said:

     | The user said | Mode |
     |---|---|
     | "never", "must not", no exceptions | `block` |
     | "warn me" / "usually not" / "unless there's a good reason" | `warn` (Claude may insist once) |
     | "ask me first" | `ask` |
     | "if you're stuck, ask me" | `retry` |
     | "stop everything if…" | `stop` |
     | "just tell me" / "flag it" | `log` |

     If the user didn't say, leave the project default (`settings.mode`) and say which it is.

4. **Test the draft** with steps that should and should not be blocked. Prefix each step with
   the expected outcome. Steps that are allowed count as executed for later steps.

   ```
   agentltl check --add /path/to/draft.yaml \
     "deny: git push --force" "allow: git push" \
     "allow: pytest" 'deny: Edit {"file_path": ".env"}'
   ```

   - Include at least one case the rule must NOT catch, such as a similar but harmless command.
     Include every exception the user stated ("adding new files is fine" → an
     `allow:` step that creates a new file).
   - Include one case per alternative way of doing the action.
   - The command exits 1 if any expectation fails. Fix the draft until it passes.
   - Any `WARNING` it prints means a tool or argument name nothing produces. Fix it: such a
     rule never fires, or, for `require`, refuses every call.

5. **Show the user** before editing anything:
   - the YAML,
   - a short table of the steps and their outcomes,
   - anything the rule cannot see (see the limits in reference.md).

   Then add the rule to `AGENTLTL.yaml`, keeping the rest of the file and its comments intact.
   Run `agentltl validate` again.

## Removing or loosening a rule

The user often describes the rule rather than naming it: "remove the rule that says I can't
push to main", "stop asking me before installing".

1. **Find it**: run `agentltl validate`. Each rule is listed with its id, mode, what it
   checks, its `why`, and in parentheses where it comes from. Match the user's words against
   all of these.
   - If several rules could be meant, ask which one, and show their ids and one-line summaries.
   - If none matches, say so and list the rules.
   - When a command was just refused, the refusal names the rule (`Rule 'x' blocked this
     call`).
2. **Confirm** before changing anything. Name the rule, quote its `why`, and say what will be
   allowed from now on.
3. **Change it** according to where it comes from:

   | Comes from | To remove it | To loosen it |
   |---|---|---|
   | `file …/AGENTLTL.yaml` (the project's) | delete its entry under `rules:` with the Edit tool, keeping everything else | change its `mode` (e.g. `block` → `warn` or `ask`) |
   | `library:NAME` | `agentltl unuse NAME` (switches off the whole entry). For one rule of an entry with several, `agentltl disable ID` | `agentltl use NAME --mode M` |
   | `user file ~/.claude/AGENTLTL.yaml` | everywhere: delete it from that file. In this project only: `agentltl disable ID` | edit its mode in that file, or replace it here with a project rule of the same id |
   | `built-in` (`memory-first`) | `agentltl disable memory-first` | a project rule with `id: memory-first` and `mode: log` |

   `use`, `unuse`, `disable` and `enable` edit the project file. Add `--user` to edit
   `~/.claude/AGENTLTL.yaml` instead. `agentltl enable ID` undoes a `disable`.
4. **Check**: run `agentltl validate`. The rule must be gone, or show its new mode. If the user
   gave an example command, run `agentltl check "allow: <command>"` to show it now passes.

Never remove or loosen a rule on your own initiative, for example because it blocks what you
are doing. Only the user decides that. When a rule is in your way, tell the user, and let them
ask for the change.

If you came here instead of saving a memory, don't also save the rule to memory: the rule
is listed to Claude at every session start. Save to memory only the parts no rule can check.

Rules apply from the next tool call; no restart is needed. A broken file disables ALL rules,
so always validate after editing.
