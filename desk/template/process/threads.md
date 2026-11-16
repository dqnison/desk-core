---
tier: action
when:
  - paths: ['now.md', '**/now.md', '**/threads/**']
---

# Threads

A thread lasts longer than a session: an intention or an unfinished inquiry. A file
`threads/<slug>.md` (about the desk itself at the root, about a place under
`contexts/<place>/`) with `status: open` in its frontmatter and a `## Дальше`
section — the next step. The `now.md` next to the `threads/` folder lists it: a
title, one line of state and a link. `now.md` loads at session start: ≤ 4 KB.

A closed thread leaves `now.md` and gets `status: closed`, `closed: <date>` and a line
«**Закрыта <date>.** <decision>». With `threads: true` in desk.yml, `desk lint` checks it.
