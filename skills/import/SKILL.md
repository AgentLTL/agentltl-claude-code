---
name: import
description: Turn CLAUDE.md and memory into enforced rules
when_to_use: The user asks to turn CLAUDE.md, memory, project instructions or AGENTS.md into AGENTLTL rules ("scan my CLAUDE.md for rules", "which of my memories can be enforced", "import my instructions"), or picks the memory scan during /agentltl:setup.
argument-hint: "[--user: your own memory, for every project]"
---

# Turning memory into rules

What CLAUDE.md, `.claude/rules/` and auto memory say is only read, and can be forgotten.
An AGENTLTL rule is checked on every tool call. This skill finds the statements in memory
that are really rules about tool calls, and turns them into tested rules, one approval at a
time. Everything else stays in memory, where it belongs.
Arguments: `$ARGUMENTS` (`--user`: the user's own memory, whose rules go in
`~/.claude/AGENTLTL.yaml`).

The rule syntax is in [the rules skill's reference.md](${CLAUDE_PLUGIN_ROOT}/skills/rules/reference.md).
Read it before you draft anything.

## Steps

1. **Scan**: run `agentltl memory scan --json` (add `--user` if given), and `agentltl library
   --json`. The scan lists the memory files and the statements in them that read like
   instructions about actions (`candidate`), each with:
   - `id`: the statement's id, used by `from:` and `decline`;
   - `file`, `line`, `section` (the heading it sits under), `text`, and `context` (an
     auto-memory file's body: read it, the details are there);
   - `commands`: the commands it quotes, translated into the calls a rule names
     (`git_submodule_update {"init": true, "recursive": true}`). `needs_spec` lists calls
     with no spec yet, whose options a rule can't name;
   - `target`: `project` or `user`, the rule file a rule from it belongs in.

   Statements already covered by a rule (`from:`) or declined before are left out.
   `--all` shows every statement. If nothing is left, say so and stop.

   With more than about 40 statements, hand each file to a subagent with steps 2 and 3 and
   this file's path. Ask each one for its candidates as YAML, so the memory text doesn't
   fill this conversation.

2. **Sort each statement** into exactly one class:

   | Class | When | Example |
   |---|---|---|
   | `library` | a library entry already does it (on or off) | "never add Claude as co-author" → `no-claude-coauthor` |
   | `rule` | it names an action and the check is on the call: its tool, its arguments, what came before it, how many times | "`git submodule update --init` — NEVER `--recursive`" |
   | `partial` | part of it can be checked, part is judgment | "do not pip-install into a host env": a bare `pip install` can be caught, "host" can't be fully |
   | `not-a-rule` | a fact, a preference about code or prose, a plan, a judgment | "prefer vLLM over TransformersModel", "report against the same run's baseline" |

   If unsure, choose `not-a-rule`. A wrong refusal costs the user more than a missed rule,
   and memory still holds the statement. Statements about *how to write code* are almost
   never rules: AGENTLTL sees tool calls, not the code inside them.

3. **Draft a rule** for each `library`, `rule` and `partial` statement:
   - **Library**: name the entry. If it is already on and does what the statement says,
     the statement is covered: note it, and record it with step 6's `--from` only.
   - **Ground it** in real calls. Use the statement's `commands`, and `agentltl translate`
     for any other command it implies. For a `needs_spec` call, write a spec under `tools:`
     in the same draft (reference.md, "When a command needs a spec"), and translate again
     to confirm the option now has a name.
   - **Pick the mode from the words, never stricter than the text**:
     - "never", "must not", NEVER → `block`;
     - "avoid", "prefer", "usually", "don't … unless", or any exception the rule can't see →
       `warn`;
     - "ask me first", "check with me" → `ask`.
   - Write `why` from the statement's own reason (memory often has a **Why:** line), and
     `fix` from its **How to apply:**, if it has one.
   - Add `from: {file: <path>, line: <n>, id: <id>}`. The path is relative to the project
     root, or begins with `~` for files in the home directory.

4. **Test every draft** with `agentltl check --add <draft>.yaml`. Build the steps from the
   statement:
   - `deny:` (or `ask:`) the command it forbids, written as the memory writes it;
   - `allow:` each exception it states ("inside docker is fine" →
     `allow: docker compose run --rm harness pip install x`);
   - `allow:` a similar command that must pass (`git submodule update --init`).

   Fix the draft until the check passes with no WARNING. A rule that can't pass its own
   statement's cases is `not-a-rule`. Say why when you present it.

5. **Ask the user.** Use AskUserQuestion with `multiSelect: true`, one question per memory
   file, or per theme when a file has more than 4 candidates. Each option's label is the
   rule id. Its description quotes the statement briefly, then gives the mode and what gets
   refused, like this:
   `"NEVER --recursive" (CLAUDE.md:9) → block: git submodule update --recursive`.
   - For a `partial` rule, also say what stays unchecked.
   - For a `library` entry, write "packaged rule" in the description.
   - After the questions, list the `not-a-rule` statements in one short paragraph, so the
     user sees that nothing was dropped silently.
   - Without AskUserQuestion, show a numbered list and ask for the numbers.

6. **Apply what was ticked**, adding `--user` to each command when the statement's `target`
   is `user`:
   - a library entry: `agentltl use NAME --from ID`. Run it for an entry that is already
     on too: it records the statement as covered.
   - your own rules: add them under `rules:` of the target file (the project's
     `AGENTLTL.yaml`, or `~/.claude/AGENTLTL.yaml`), with their `tools:` specs, keeping the
     rest of the file;
   - for anything unticked: `agentltl memory decline ID...`, so the next scan doesn't ask
     again. `agentltl memory forget ID` undoes it.

   Then run `agentltl validate`. Each new rule shows `from <file>:<line>`.

7. **Leave the memory as it is**, unless the user asks otherwise. It still helps Claude
   plan before acting; the rule refuses the call if the plan goes wrong.
   - Offer once: "Replace the converted lines in memory with a pointer to the rule?"
   - If they say yes, replace each converted statement with
     `(enforced by AGENTLTL rule <id>)`. The `memory-first` rule warns once on that
     edit. That's expected: repeat the same call.
   - Never delete a memory file.

8. **Summarise**: the number of rules added and their modes, the specs added, what was
   declined, and what stays in memory as not-a-rule. Running `/agentltl:import` again
   later only shows what's new.

Rules apply from the next tool call; no restart is needed.
