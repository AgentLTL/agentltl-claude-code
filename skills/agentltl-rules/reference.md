# AGENTLTL.yaml reference

```yaml
settings:                 # all optional
  mode: block             # default mode for rules without one
  retries: 3              # blocked attempts allowed by mode: retry before the user is asked
  unparseable:            # shell commands the guard cannot analyse (eval, `cmd &`, $CMD args...)
    interactive: ask      # ask | note | allow | deny, in normal permission modes
    auto: note            # in auto / bypass mode: note = let through, tell Claude it was unchecked
  announce: true          # list the rules to Claude at session start and after compaction
  scope: session          # default memory for rules: session | project (see Memory)
  memory_first: true      # refuse memory writes once, steering rules into this file

rules:
  - id: short-kebab-id    # required, unique
    <kind>: ...           # exactly one kind, see below
    why: ...              # shown to Claude when it is blocked; say the reason, not the rule
    fix: ...              # optional: what to do instead
    mode: block           # optional, see Modes
    scope: session        # optional, see Memory

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

Use `agentltl translate "<command>"` to see names and arguments; `agentltl validate` warns
about a tool or argument name that nothing produces (such a rule never fires, or, for
`require`, fires on every call). Commands with no spec become a
tool named after the executable, with a single `argv` list (`where: {argv: "--prod"}`). Output
redirections (`> file`, `>> file`, `2> file`, `&> file`, `cat <<EOF > file`) appear as
`redirect_to`, input redirections (`< file`) as `redirect_from`. `patch` and `git apply` list
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

It reads as `now(Edit, file_path=f) -> called(Read, file_path=f)`, for every `f`. It compiles
to AgentLTL's `ForAll` + `Var` + `CalledWith`, over the values in the call being checked.

- Path arguments are made absolute first, so `rm a.py` and `Read /proj/a.py` match.
- Use one variable, and bind it on every target of both sides.
- On the `first` side, give only `with` values (AgentLTL compares them for equality), and use a
  single-valued argument. `cat`'s `paths` is a list and never equals one file;
  `agentltl validate` warns about it.
- `since` cannot be combined with a variable yet.

## Rule kinds

| Kind | Meaning |
|---|---|
| `never: T` | No call matching T. `with:`/`where:` may sit at rule level. |
| `before: [A, B]` | A call matching B needs an earlier call matching A (in the rule's memory). |
| `before: {first: A, then: B, since: S}` | The A must come after the last call matching S. This is "run tests after your last edit". |
| `require: T` | When one of T's tools is called, its arguments must match T's `with`/`where`. |
| `at_most: {call: T, times: n}` | At most n calls matching T (in the rule's memory). |
| `ltl: '<formula>'` | Raw AgentLTL. `now("x")`, `called("x")`, `before("a","b")`, `G`, `X`, `U`, `!`, `&`, `\|`, `->` |
| `formula: {type: ..., args: ...}` | Structured AgentLTL, e.g. `{type: Before, args: {a: x, b: y}}` |

How rules are evaluated:

- The kinds other than `ltl` and `formula` judge **only the call being made**. A rule broken
  earlier, for example by an override, never blocks unrelated later calls.
- `ltl` and `formula` use AgentLTL's own semantics over the whole trace of the rule's memory.
  `now("x")` means "the call at this step is x"; `called("x")` means "x appears anywhere in
  the trace". Under `G` and `X` you almost always want `now`:
  `G(now("deploy") -> X(G(!now("deploy"))))` is "deploy at most once".
- Formulas that AgentLTL classifies as unsafe to enforce (liveness properties) are rejected,
  because the guard acts on each call as it is made and cannot wait for the session to end.
  That covers `F(called("pytest"))`, a bare `called("x")`, and a bare `before("a", "b")`.
  Guard the formula with `G(now(...) -> ...)`, or use a rule kind (`before: [a, b]`).

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
| `retry` | denied; after `retries` refusals the user is asked | the user, after the retries |
| `ask` | the user gets a permission prompt with the reason | the user |
| `stop` | denied and Claude stops working | the user |
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

  # "At most one migration per task; if stuck, ask me"
  - id: one-migration
    at_most: {call: alembic_revision, times: 1}
    mode: retry

  # Raw LTL: rebase only after fetching
  - id: fetch-before-rebase
    ltl: 'G(called("git_rebase") -> before("git_fetch", "git_rebase"))'
    why: Rebasing onto a stale upstream causes conflicts later.

tools:   # teach the translator a project command, so rules can name its arguments
  alembic:
    subcommands:
      revision:
        options:
          - {flags: [-m, --message]}
          - {flags: [--autogenerate], type: bool}
```

## Limits (tell the user when they matter)

- **Unanalysable commands are not checked.** `eval`, `cmd &`, `$CMD args`, and shell functions
  fall under `settings.unparseable`. `if`, `while`, and loops over runtime lists are checked
  with every command they might run, listed once.
- **No visibility into scripts or programs.** The guard does not see inside `bash script.sh`,
  `make target`, `npm run x`, or `python -c "..."`. It sees only the command. Name those
  commands in the rule too, for example `{tool: make, with: {argv: test}}` in the `first:` list.
- **Only calls that ran count as done.** A call is recorded once it has run, so a denied call,
  or one you refused, never counts toward `before`.
- **Session memory starts empty in each new session.** Use `scope: project` when the rule
  should remember earlier sessions.
- **`cd` inside a command line is not followed** (to do), and **`exists` re-judges earlier calls
  against the disk as it is now** (to do). Mention them when a rule depends on either.
