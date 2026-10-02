---
name: agentltl
description: Show the AgentLTL for Claude Code's status for this project, including the rules in force, what it recorded this session, and what it blocked. It can also reset the session trace.
argument-hint: "[status | trace | reset | check <command>...]"
disable-model-invocation: true
---

Report on the AgentLTL for Claude Code. Requested: `$ARGUMENTS` (empty means `status`).

- **status**:
  1. Run `agentltl validate`, then `agentltl trace`.
  2. Summarise in a few lines:
     - which rules are in force, and their modes;
     - how many calls are recorded;
     - the recent interventions, and why.
  3. If the rule file has errors, show them first. Until they are fixed, nothing is enforced.
- **trace**: run `agentltl trace` and show the output.
- **reset**:
  1. Confirm with the user first, because it forgets which calls already happened. For example,
     tests already run will no longer count toward `before` rules.
  2. Then run `agentltl reset`.
- **check <steps>**: run `agentltl check <steps>` and explain each outcome.

To pick packaged rules, use the `agentltl-setup` skill. To add, change or remove rules, use
the `agentltl-rules` skill.
