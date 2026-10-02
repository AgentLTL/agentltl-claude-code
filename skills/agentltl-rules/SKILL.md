---
name: agentltl-rules
description: Turn a natural-language rule ("never force-push", "run the tests before pushing", "don't touch .env") into AgentLTL rules in AGENTLTL.yaml, test them, and add them. Use when the user asks to add, change, explain or debug an AGENTLTL rule, or to translate a policy into AgentLTL / LTL constraints for this project.
argument-hint: "<rule in plain words>"
---

# Writing AGENTLTL rules

The agentltl-guard plugin checks every tool call Claude makes against `AGENTLTL.yaml` at the
project root (and `~/.claude/AGENTLTL.yaml` for rules that apply everywhere). Shell commands are
parsed into structured calls first: `git commit -am x && git push -f` is checked as
`git_commit{message: x, all: true}` then `git_push{force: true}`.

The user's request: $ARGUMENTS

Rule kinds, target syntax, modes and worked examples are in
[reference.md](${CLAUDE_PLUGIN_ROOT}/skills/agentltl-rules/reference.md). Read it before you
write a rule.

## Steps

1. **Read the current rules**: run `agentltl validate`. If there is no `AGENTLTL.yaml` yet,
   you will create one at the project root.

2. **Find the real tool names and arguments.** Rules refer to the names the translator produces,
   not to the words in the command.
   - Run `agentltl translate "<a command the rule is about>"` for one or two typical commands,
     for example `agentltl translate "git push --force origin main"`.
   - Run `agentltl tools 'git_*'` to list the tools and their arguments.
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

Rules apply from the next tool call; no restart is needed. A broken file disables ALL rules,
so always validate after editing.
