"""AgentLTL rules in front of Claude Code tool calls.

The rules, the guard, the hooks' logic and the library are agentltl_coding's; this package is
the Claude Code side: the hook I/O, the status line, where memory lives, and the harness
description below.
"""

from agentltl_coding import Harness, configure

# Files Claude Code loads as memory, and the ways a call writes them.
MEMORY_FILES = ["CLAUDE.md", "CLAUDE.local.md", "*/.claude/rules/*",
                "*/.claude/projects/*/memory/*"]
MEMORY_FIRST = {
    "id": "memory-first",
    "never": [{"tool": ["Write", "Edit", "MultiEdit"], "where": {"file_path": MEMORY_FILES}},
              {"tool": "*", "where": {"redirect_to": MEMORY_FILES}},
              {"tool": ["tee", "sponge"], "where": {"*": MEMORY_FILES}}],
    "mode": "warn",
    "why": "AGENTLTL rules are enforced on every call; memory can be forgotten. If what you are "
           "saving says which tool calls or commands to make, avoid, or make first (never X, "
           "always Y before Z, at most N times, only with these arguments), add it to "
           "AGENTLTL.yaml instead, using the /agentltl:rules skill, and leave it out of memory.",
    "fix": "Write the rule with the /agentltl:rules skill. Keep in memory only what no rule can "
           "check (facts, preferences, style). If nothing here can be a rule, repeat this exact "
           "call to save it.",
}
MEMORY_NOTE = ("before you save anything to memory (CLAUDE.md, CLAUDE.local.md, .claude/rules/, "
               "auto memory), ask whether it is a rule about tool calls or commands. If it is, "
               "add it to AGENTLTL.yaml with the /agentltl:rules skill instead: rules there are "
               "enforced, memory can be forgotten.")


def _memory(root, user_only, home):
    from .memory import claude_sources
    return claude_sources(root, user_only, home)


CLAUDE_CODE = Harness(
    name="claude-code",
    agent="Claude",
    user_dir="~/.claude",
    shell_tools={"Bash": "command"},
    builtins={"memory_first": MEMORY_FIRST},
    auto_modes=("auto", "bypassPermissions", "dontAsk"),
    project_env="CLAUDE_PROJECT_DIR",
    skill="/agentltl:{}",
    memory=_memory,
    memory_note=MEMORY_NOTE,
)
configure(CLAUDE_CODE)
