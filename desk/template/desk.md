---
tier: import
---

# Desk

How I work with the assistant. This file is the entry: `@`-imported into every
session (tier `import`), so it stays short.

## Principles

1. The scope is what we agreed on: side edits only when small.
2. A task statement is a hypothesis: check it against the code before the work.

## Tiers

Each rule says in its frontmatter when it must be in context:

- `import` — in every session, whole (this file);
- `session` — before the first action of a session (the place's hub, the task loop);
- `action` — no later than the action its `when:` names;
- `link` — reference by link; the hook does not deliver it.

## Map

- `process/` — portable rules, no names of a workplace.
- `contexts/<place>/` — what belongs to a place: hosting, tracker, repositories;
  `context.md` says by `match:` when the place is active.
- `contexts/<place>/projects/<repo>/` — facts of one repository.
- `ext/` — checks in code where data cannot express them.
- [now.md](now.md) — open threads.
