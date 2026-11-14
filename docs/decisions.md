# Decisions

Short records of what was checked or chosen while building the core, and why.

## Plugin spike (step 0)

Question: does a Claude Code plugin with hooks load from a local directory, and
which events does it receive?

Checked with Claude Code 2.1.x, `claude -p --plugin-dir <dir>`, nothing installed
into the user configuration. The plugin registered SessionStart, UserPromptSubmit,
PreToolUse, PostToolUse, Stop, SessionEnd and PreCompact through
`hooks/hooks.json`, each command `python3 "${CLAUDE_PLUGIN_ROOT}/…"`.

- `claude plugin validate` accepts the manifest.
- Received in a session: SessionStart (`source: startup`), UserPromptSubmit
  (`prompt`, `permission_mode`, `prompt_id`), SessionEnd (`reason`).
  `additionalContext` of SessionStart and UserPromptSubmit was accepted.
- `CLAUDE_PLUGIN_ROOT` points at the plugin directory; `CLAUDE_PLUGIN_DATA` at a
  per-plugin data directory (`~/.claude/plugins/data/<name>-inline`);
  `CLAUDE_PROJECT_DIR` is set.
- Not observed in a session: PreToolUse and PostToolUse. The headless run could
  not reach the model (expired login), so no tool was called. Plugin hooks use the
  same `hooks.json` schema and event set as settings hooks, so they are expected to
  work; verify on the first real install.

Outcome: the plugin is the primary install path. The fallback from the design is
kept anyway — `desk init --hooks` prints the same hooks as a `settings.json` block.

## One hook entry point

The old binding had seven hook scripts. Every hook input carries
`hook_event_name`, so the core has one entry, `bin/desk-hook`, dispatching on it.
SessionStart is still registered several times (`--slot N --slots K`): each
registration is a separate output, and an output longer than ~10k characters is
hidden by the harness in a persisted file.

## Deviations from the design layout

- `desk/hooks/` (one module per event) became one `desk/hook.py`: the events
  share state, lock and packing, and the input names the event.
- `desk/checks.py` added: the universal checks that need code are registered
  through the same `desk.ext` API as user extensions — the API is proven by the
  core's own use.
- Two of the five universal checks are data, not code: commit attribution
  (`deny` in `commit-message`) and subject format (`deny-unless` in
  `commit-subject`, length and free prefixes inside the regex). The third,
  "sensitive rule read before the edit", is the derived compliance of any `ask`
  guard (`in: paths`), so it holds for every rule of that shape, not one file name.
- A gate (`when: … gate: true`) fires only while the rule is not in context. The
  old binding kept a separate "opened by prompt" mark; "already delivered" covers
  it and also covers a rule read by hand.
- `deny` carries the rule text like `ask` and the gate do; the old deny sent only
  the reason.
- `skip_in:` takes context names plus `desk`, not a fixed pair.
- Contexts declare `match: {remote, path, default}`; the old code scraped
  backticked domains out of the hub's prose and hardcoded the `personal` fallback.
- Paths are compared as paths: the old `DESK in path` substring test counted
  `~/desk-core/…` as a desk file.
- Globs: `*` does not cross `/` (fnmatch did).
- The PostToolUse report (what a command really changed) runs the same `when:`
  and guards on synthetic Write calls for the changed files; guard hits there
  become remarks ("done without confirmation").
- Hook state lives in `$TMPDIR/desk-core/` (or `DESK_STATE_DIR`), apart from the
  old `desk-hook/`, so both bindings can run side by side.
- Not carried over (policy, stays private): worktree and docker sweep, tracker and
  MR checks, CLI-skill checks, desktop header, SessionEnd journal, Obsidian canvas,
  the interfaces dictionary, link-map/hub/threads/freshness lint checks.

## Changes driven by the replay (step 5)

The replay ran the old trigger registry and the `when:` rewrite of it over every
local transcript (392 sessions, ~65k calls); the report stays in the private desk.
Engine changes it caused:

- `command` and `input` regexes are case-sensitive (the old `in` substring tests
  were); `prompt` stays case-insensitive.
- `desk: true` — any command from a shell already in DESK_HOME, writes into it,
  or a git command that changes something pointed there (`cd`, `git -C`). The first
  version counted `cd ~/desk && grep …` from another session: reading, not touching.
- Repository rules follow the touched repository into a session of another context
  (paid code stays guarded wherever the edit comes from); only a repo name known to
  two contexts falls back to the active one.
- Unparsable YAML in a file without `tier:` (a skill manifest) is not a lint error;
  block scalars `|`/`>` are parsed.
- A `git commit` line inside a heredoc body is not a commit: the old guard denied a
  script that merely contained such a line.

Result of the final replay: 10 of 14 triggers identical, the sensitive-path one
17 of 18; 96.2 % of firings agree, and every remaining divergence is a deliberate
difference (the old trigger fired on reads, on a sibling directory, on the word
`worktree` in text; the new one sees writes through scripts, `mkdir`, symlinks).
Guards: 2 518 commit commands, the only 4 differences are the old false denies on
heredoc text.

## Moving the author's desk onto the core

- Hooks are a `settings.json` block pointing at `bin/desk-hook` of this checkout, not
  the installed plugin: the plugin is copied into the plugin cache, so every engine
  change would need an update; the block also rolls back as one key.
- Confirmed live after the switch: SessionStart (slot 0 header + file slots),
  UserPromptSubmit delivery, PreToolUse delivery and `deny` with the rule attached.
- Engine additions it needed: extension events (`ext.on`) for the session header,
  checkout state and journal; named triggers and settings in `desk.yml` (the entry
  rule is imported into every session and must not carry engine data); `also:` for
  shared rules a repository rule brings; the header in its own start output (slot 0)
  so header and files never share one ~10k-character output; a reset mark so the
  parallel start slots reset the state once.

## Independent review (14.11) and what changed

A fresh reviewer read the live engine. Confirmed and fixed:

- Guards looked at the tail of the command from `git commit` to its end: `git commit
  --amend --no-edit && python3 -m unittest` was denied (the `-m` of unittest), `echo
  'Generated with'` after a clean commit was denied, `-am`, `HUSKY=0 git commit`,
  `if git commit`, `(git commit` were not checked. Now a shell line is split into
  simple commands (quotes, `$(…)`, heredoc bodies, groups) and each `git commit`
  is judged alone; the message flag may sit in a short-flag group.
- MR attribution was missed for the CLI run through a venv interpreter: the private
  check now looks at any shell segment that creates a MR, and at MCP MR tools.
- `~nouser` paths raised inside the hook and turned all guards off for that call.
- Every Bash call snapshotted `git status` of DESK_HOME too: a parallel session's
  edit in the desk came back as this command's change. Now only checkouts the
  command works in, writes to or names; `GIT_OPTIONAL_LOCKS=0`, `-z` parsing.
- `~/desk/…` and `$HOME/desk/…` in a command counted as work in a repository named
  in the path; URLs too.
- Smaller: the start header ran extension code under the session lock; a deny did
  not queue the tail of a big rule; `DESK_NO_DENY` did not open gates; an absolute
  glob lost its trailing slash; stale snapshots of calls that never ran piled up.

Kept on purpose: `git diff` fires the `git` trigger (the git rule covers comparing
branches); `touches_home` treats it as reading.

The session header moved into the core (`desk/header.py`); extensions add rows
and signals. The core has its own pre-commit running the tests.
