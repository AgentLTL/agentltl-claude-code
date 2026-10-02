---
name: setup
description: Pick ready-made rules to switch on
when_to_use: First setup of AgentLTL, or when the user asks which AGENTLTL rules are available, wants to browse, switch on or switch off packaged rules from the plugin's library, or says "set up agentltl".
argument-hint: "[--user: for every project]"
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
   - Offer a bundle (an entry tagged `bundle`, such as `devops-secrets`) as one option that
     switches on all the entries it lists.
   - Mark entries already on with "(on)" in the description. Tell the user that unticking one
     switches it off.
   - If there are more entries than fit, ask in several rounds.
   - Without AskUserQuestion, show a numbered list and ask for the numbers.

4. **Ask about strictness** only if the user wants it. Every entry has a sensible default
   mode. To change it, use `agentltl use NAME --mode warn`. The modes are block, warn, ask,
   retry, stop and log; see the `/agentltl:rules` skill's reference.md.

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

7. **Offer custom rules**: for anything the library doesn't cover, use the `/agentltl:rules`
   skill to write a rule from the user's own words.

Changes apply from the next tool call. No restart is needed.
