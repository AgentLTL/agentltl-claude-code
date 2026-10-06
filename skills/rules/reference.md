# AGENTLTL.yaml reference

```yaml
settings:                 # all optional
  mode: block             # default mode for rules without one
  retries: 3              # blocked attempts allowed by mode: retry before the user is asked
  retry_counting: cumulative  # or consecutive (an allowed call resets), hybrid (either)
  report: 3               # how many broken rules of the same mode one refusal lists
  finish_retries: 2       # times per turn `finally` rules send Claude back before it finishes
  unparseable:            # shell commands the guard cannot analyse (eval, `cmd &`, $CMD args...)
    interactive: ask      # ask | note | allow | deny, in normal permission modes
    auto: note            # in auto / bypass mode: note = let through, tell Claude it was unchecked
  announce: true          # list the rules to Claude at session start and after compaction
  scope: session          # default memory for rules: session | project (see Memory)
  memory_first: true      # refuse memory writes once, steering rules into this file
  scan_output: true       # after each call, warn when its output contains a credential

use:                      # packaged rules from the plugin's library (`agentltl library`)
  - no-force-push
  - {tests-before-push: {mode: warn}}   # with another mode (or scope)
disable: [memory-first]   # switch rules off by id: from use:, ~/.claude/AGENTLTL.yaml, or built in

rules:
  - id: short-kebab-id    # required, unique; the same id as a packaged rule replaces it
    <kind>: ...           # exactly one kind, see below
    why: ...              # shown to Claude when it is blocked; say the reason, not the rule
    fix: ...              # optional: what to do instead
    mode: block           # optional, see Modes
    scope: session        # optional, see Memory
    from: {file: CLAUDE.md, line: 9, id: 674054a6cf}   # optional: the memory statement it
                          # enforces (`agentltl memory scan` ids); `validate` shows it, and
                          # the scan stops proposing that statement

tools:                    # optional cli-to-tools specs for project commands (see below)
```

## Targets

A target says which calls a rule is about.

| Form | Matches |
|---|---|
| `git_push` | any `git push` |
| `[Edit, Write]` | either tool |
| `"kubectl_*"` | any tool whose name matches the glob (every kubectl subcommand) |
| `{tool: git_push, with: {force: true}}` | exact argument values |
| `{tool: [Edit, Write], where: {file_path: "*.lock"}}` | glob on argument values |
| `{tool: [rm, cat], where: {"*": "**/.env"}}` | glob on ANY argument |
| `{tool: [Edit, Write, rm], where: {"*": "migrations/*"}, exists: true}` | only paths that already exist (so creating a new file is not caught) |
| `{tool: pytest, succeeded: true}` | only calls that ran and succeeded (exit status 0); `false`: only failed ones |
| `[{tool: Write, where: {...}}, {tool: rm, with: {...}}]` | any of several targets |

How matching works:

- **`with`** compares values for equality. A list argument matches if it contains the value.
  `false` also matches a flag that is absent.
- **`where`** globs: `*` also matches `/`.
  - Paths are tried as written, as absolute paths, relative to the project root, and by basename
    when the pattern has no `/`.
  - Several patterns in a list mean any of them.
- **Several keys** in `with` or `where` must ALL match.
- **Files known only at run time** (`ls | xargs rm`, `find . -exec rm {} +`, `rm $UNSET`)
  count as a *possible* match for a path condition. `never` refuses them, `require` cannot
  confirm them so it refuses too, and they never satisfy the `first` side of a `before`.
  `F=x; rm $F` and `$HOME` are resolved, so they are judged normally.
- **`exists`** is checked on disk when the call is made. Earlier calls in the trace are judged
  against the disk as it is now.
- **`succeeded`** reads how a call that ran ended: a Bash command that exits non-zero has
  failed. A call that has not run yet may or may not succeed, so put `succeeded` on the side
  of a rule that looks back (`before`'s `first`, `finally`), not on the call being made.

Use `agentltl translate "<command>"` to see names and arguments; `agentltl validate` warns
about a tool or argument name that nothing produces (such a rule never fires, or, for
`require`, fires on every call). Commands with no spec become a
tool named after the executable, with a single `argv` list (`where: {argv: "--prod"}`). Output
redirections (`> file`, `>> file`, `2> file`, `&> file`, `cat <<EOF > file`) appear as
`redirect_to`; the ones that truncate the file (all but `>>`) also as `overwrite_to`. Input
redirections (`< file`) appear as `redirect_from`. `patch` and `git apply` list
the files their diff modifies as `paths`, and `curl -O` its output file as `output`.

Runners and package managers (`make`, `npm`, `yarn`, `cargo`, `go`, `uv`, `poetry`, `conda`,
`apt`, `brew`) have no spec on purpose: name them as `{tool: npm, with: {argv: install}}`.
CLIs with specs name their subcommands: `kubectl_delete`, `terraform_apply`, `gh_pr_merge`,
`aws_s3_rm`, `git_apply`; the in-place editors `sed`, `perl` and `awk` expose `in_place` and
`paths`.

## Variables: the same value in two calls

A `with` value written `$name` is a variable. In a `before` rule it ties the two calls to the
same value, implicitly for every value. "Read a file before you overwrite it":

```yaml
- id: read-before-overwrite
  before:
    first: {tool: Read, with: {file_path: $f}}
    then:
      - {tool: [Edit, Write], with: {file_path: $f}, exists: true}   # new files are fine
      - {tool: rm, with: {paths: $f}}
```

It reads as "for every `f` the call being made has, an earlier `Read` had `file_path` `f`".
It compiles to AgentLTL's `ForAll`, over the values in the call being checked.

- Path arguments are made absolute first, so `rm a.py` and `Read /proj/a.py` match.
- Bind every variable on every target of both sides. Several variables must match together:
  `{chart: $chart, version: $v}` needs an earlier call with that same chart and version. With
  list arguments, every combination of values needs an earlier call.
- On the `first` side, give only `with` values. A list argument matches when it contains the
  value: after `cat a b`, `a` has been read.
- `since` cannot be combined with a variable yet.

## Rule kinds

| Kind | Meaning |
|---|---|
| `never: T` | No call matching T. `with:`/`where:` may sit at rule level. |
| `before: [A, B]` | A call matching B needs an earlier call matching A (in the rule's memory). |
| `before: {first: A, then: B, since: S}` | The A must come after the last call matching S. This is "run tests after your last edit". |
| `require: T` | When one of T's tools is called, its arguments must match T's `with`/`where`. |
| `at_most: {call: T, times: n}` | At most n calls matching T (in the rule's memory). |
| `finally: T` | Before Claude finishes its turn, a call matching T has run. Never refuses a call: at the end of the turn Claude is sent back to do it (at most `finish_retries` times per turn). |
| `finally: {call: T, since: S}` | ... a call matching T has run after the last call matching S. "Run the tests before handing back work you edited." |
| `ltl: '<formula>'` | Raw AgentLTL. `now("x")`, `called("x")`, `before("a","b")`, `G`, `X`, `U`, `!`, `&`, `\|`, `->`, and looking back: `Y` (previous call), `O` (once), `H` (always so far), `S` (since) |
| `formula: {type: ..., args: ...}` | Structured AgentLTL, e.g. `{type: Before, args: {a: x, b: y}}` |

How rules are evaluated:

- Every kind compiles to an AgentLTL formula. `never`, `before`, `require` and `at_most` look
  back from the call being made (`G(matches(B) → Y O matches(A))`), so AgentLTL judges them
  on **that call alone**: a rule broken earlier, for example by an override, never blocks
  unrelated later calls.
- `ltl` and `formula` use AgentLTL's own semantics over the whole trace of the rule's memory.
  `now("x")` means "the call at this step is x"; `called("x")` means "x appears anywhere in
  the trace". Under `G` and `X` you almost always want `now`:
  `G(now("deploy") -> X(G(!now("deploy"))))` is "deploy at most once".
- A formula is refused for a call only for what that call newly breaks. After a `warn`
  override, `G(now("deploy") -> X(G(!now("deploy"))))` doesn't refuse `ls`, but still
  refuses the next deploy.
- Two kinds of formula are rejected, because the guard judges each call as it is made:
  - one that fails until some call happens, such as a bare `called("x")`: every call
    before x would be refused. Say when it applies: `G(now("deploy") -> called("pytest"))`.
  - one that can't fail before the session ends, such as `F(called("pytest"))` or
    `G(now("a") -> F(now("b")))`: it would never refuse anything. Bound it with
    `within_steps("a", "b", 3)`.

  `before("a", "b")` is accepted: it fails, for good, at the first b with no a before it.

## Memory

| `scope` | Remembers | Resets |
|---|---|---|
| `session` (default) | Calls made in this Claude Code session | With each new session; compaction and resume keep it |
| `project` | Every call made in this project, across sessions | Never on its own; `agentltl reset --project` |

Pick `session` for "since you started working" rules (tests before pushing, one migration per
task). Pick `project` for facts that stay true (a one-time setup step, a release cap). When the
user says "ever", "already", "once per project" or "in any session", that is `project`.

## Modes

| Mode | What happens on a violation | Who can override |
|---|---|---|
| `block` | denied, every time | only the user (edit the rule, or run the command themselves) |
| `warn` | denied once, with the reason | Claude, by repeating the exact same call next |
| `retry` | denied; the `retries`-th try (default: the third) asks the user | the user, after the retries |
| `ask` | the user gets a permission prompt with the reason | the user |
| `stop` | denied, and no tool runs until the user replies (Claude explains); a possible match (files known at run time) is only denied | the user |
| `log` | allowed; Claude is told it broke the rule | n/a |

When one call breaks several rules, the strongest mode decides (stop > block > ask > retry >
warn > log).

## Examples

```yaml
rules:
  # "Run the tests before pushing, and again if you changed code since"
  - id: tests-before-push
    before:
      first: [pytest, {tool: make, with: {argv: test}}, {tool: npm, with: {argv: test}}]
      then: git_push
      since: [Edit, Write]
    why: CI is slow and a red main blocks everyone.
    fix: Run pytest after your last edit, then push.

  # "Never force-push, unless you really have to"
  - id: no-force-push
    never: git_push
    with: {force: true}
    why: Force-pushing rewrites shared history.
    mode: warn

  # "Don't touch .env files"
  - id: no-secrets
    never: [Edit, Write, Read, cat, cp, mv, rm, sed, tee, echo, printf, head, tail, grep]
    where: {"*": ["**/.env", "**/.env.*"]}
    why: .env files hold credentials.
    mode: stop

  # "Only delete things inside build/"
  - id: rm-only-in-build
    require: {tool: rm, where: {paths: [build, "build/**"]}}
    why: Everything else is source or data.

  # "Ask me before installing packages"
  - id: ask-before-install
    never:
      - pip_install
      - {tool: [npm, yarn, apt_get], with: {argv: install}}
    why: Dependencies need review.
    mode: ask

  # "Don't throw away work"
  - id: no-hard-reset
    never: [{tool: git_reset, with: {hard: true}}, {tool: git_clean, with: {force: true}}]
    why: Uncommitted work is lost for good.
    mode: warn

  # "Don't push unless the tests passed"
  - id: green-before-push
    before: {first: {tool: pytest, succeeded: true}, then: git_push, since: [Edit, Write]}
    why: A red build blocks everyone.

  # "Before you hand back work you edited, run the tests"
  - id: tests-before-finishing
    finally: {call: pytest, since: [Edit, Write]}
    why: Work handed back untested is work I have to test.
    fix: Run pytest now.

  # "At most one migration per task; if stuck, ask me"
  - id: one-migration
    at_most: {call: alembic_revision, times: 1}
    mode: retry

  # Raw LTL: rebase only after fetching
  - id: fetch-before-rebase
    ltl: 'before("git_fetch", "git_rebase")'
    why: Rebasing onto a stale upstream causes conflicts later.

tools:   # teach the translator a project command, so rules can name its arguments
  alembic:
    subcommands:
      revision:
        options:
          - {flags: [-m, --message]}
          - {flags: [--autogenerate], type: bool}
  helm:
    options:
      - {flags: [-n, --namespace], global: true}   # accepted before or after the subcommand
    subcommands:
      upgrade: {positionals: [{name: release}, {name: chart}]}
  kubectl:   # already bundled: this ADDS rollout's arguments, the rest of kubectl stays
    subcommands:
      rollout: {positionals: [{name: action}, {name: resource}]}
```

### When a command needs a spec

The specs under `tools:` are used everywhere the rules are: on Claude's Bash calls, and by
`agentltl translate`, `check` and `tools`. Add or extend one when `agentltl translate` shows
that the argument a rule needs is not named:

- **The command has no spec:** the result is `{"argv": [...]}`. The lint warns "has no spec".
- **The flag is unknown:** it lands in `extra_args`.
- **A value went to the wrong argument:** for example, the namespace appears in `names`.

How the spec format works:
- **Extending a bundled spec:** a spec for a command that is already bundled extends it.
  Options are merged by flag, and subcommands one by one. Write `extend: false` to replace it
  completely.
- **Flags after the subcommand:** mark them `global: true` when the real CLI accepts them
  after the subcommand (kubectl, helm, gh, aws). git's `-C` is not global.
- **Testing the spec:** after adding one, re-run `agentltl translate` to check it. Keep it in
  the same `AGENTLTL.yaml` as the rule. In `~/.claude/AGENTLTL.yaml`, it applies in every
  project.

## Limits (tell the user when they matter)

- **Unanalysable commands are not checked.** `eval`, `cmd &`, `$CMD args`, and shell functions
  fall under `settings.unparseable`. `if`, `while`, and loops over runtime lists are checked
  with every command they might run, listed once.
- **No visibility into scripts or programs.** The guard does not see inside `bash script.sh`,
  `make target`, `npm run x`, or `python -c "..."`. It sees only the command. Name those
  commands in the rule too, for example `{tool: make, with: {argv: test}}` in the `first:` list.
- **Only calls that ran count as done.** A call is recorded once it has run, so a denied call,
  or one you refused, never counts toward `before`. A command that ran and failed is recorded
  as failed: it counts unless the rule says `succeeded: true`.
- **Session memory starts empty in each new session.** Use `scope: project` when the rule
  should remember earlier sessions.
- **`exists` re-judges earlier calls against the disk as it is now** (to do). Mention it when
  a rule depends on it. (`cd` within a command line is followed: `cd sub && rm a` is
  `sub/a`.)
