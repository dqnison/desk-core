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
