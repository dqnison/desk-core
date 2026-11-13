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
