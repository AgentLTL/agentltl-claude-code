---
name: agentltl-setup
description: Let the user pick AGENTLTL rules from the plugin's library of ready-made rules (no force-push, tests before push, subagents on Sonnet, protect .env, ask before installing...) and switch them on for this project or every project. Use on first setup, or when the user asks what rules are available, wants to browse or change their packaged rules, or says "set up agentltl".
argument-hint: "[--user]"
---

# Choosing packaged rules

The plugin ships a library of tested rules. Switching one on adds its name to the `use:` list
of `AGENTLTL.yaml`; the rule itself stays in the plugin and gets fixes with plugin updates.
Arguments: `$ARGUMENTS` (`--user` means rules for every project, in `~/.claude/AGENTLTL.yaml`).

## Steps

1. **List the library**: run `agentltl library --json`. Each entry has a `name`, a `summary`,
   `tags`, `in_use` (already on in a file that applies here) and its rule ids.

2. **Ask where the rules go**, unless `--user` was given: this project (`AGENTLTL.yaml` at the
   project root) or every project (`~/.claude/AGENTLTL.yaml`). Rules for one repository's
   workflow (tests, deploys) usually belong to the project; personal habits (no Claude
   co-author, subagents on Sonnet) to every project.

3. **Let the user choose.** Use the AskUserQuestion tool with `multiSelect: true`. It takes
   at most 4 questions per call, and at most 4 options per question.
   - Group the entries by theme using their tags, for example "Git safety", "Files and
     secrets", or "Workflow and cost".
   - Each option's label is the entry's `name`, and its description is the `summary`.
   - Mark entries already on with "(on)" in the description. Tell the user that unticking one
     switches it off.
   - If there are more entries than fit, ask in several rounds.
   - Without AskUserQuestion, show a numbered list and ask for the numbers.

4. **Ask about strictness** only if the user wants it. Every entry has a sensible default
   mode. To change it, use `agentltl use NAME --mode warn`. The modes are block, warn, ask,
   retry, stop and log; see the `agentltl-rules` skill's reference.md.

5. **Apply the choices**, adding `--user` for every project:
   - `agentltl use NAME...` for the newly ticked entries;
   - `agentltl unuse NAME...` for the ones the user unticked.

   These commands edit only the `use:` list and keep the rest of the file. They create the file
   if it is missing.

6. **Check the result**: run `agentltl validate`. Then show the user the rules now in force,
   with each rule's mode and one line on what it does.
   - If a chosen rule depends on the project, say so. For example, `tests-before-push` knows
     common test runners (pytest, npm test, cargo test, make test...). If the project tests
     differently, offer to write a project rule with the same id: it replaces the packaged one.
   - `agentltl library NAME` shows a rule's YAML in full.

7. **Offer custom rules**: for anything the library doesn't cover, use the `agentltl-rules`
   skill to write a rule from the user's own words.

Changes apply from the next tool call. No restart is needed.
