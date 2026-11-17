---
tier: action
when:
  - paths: ['now.md', '**/now.md', '**/threads/**']
---

# Threads

A thread is what lasts longer than one session: an intention ("let's do it later"),
an inquiry that ended without a decision, an agreement for later. It exists so that
what was said in one chat is known in the next: `now.md` is a `session` rule, and
every session starts with the list of open threads. A task from the tracker is not
a thread, nor a fact about a project, nor a rule.

**Open** one in the same session it came up, without being asked: a file
`threads/<slug>.md` (about the desk itself at the root, about a place under
`contexts/<place>/`) with `status: open` in its frontmatter and a `## Next` section —
the next step; and a line in the `now.md` next to that `threads/` folder: a title,
one line of state and a link. `now.md` stays under 4 KB: details go to the thread file.

**Close** it when it ends in a decision: it leaves `now.md` and gets `status: closed`,
`closed: <date>` and a line «**Closed <date>.** <decision>».

With `threads: true` in desk.yml, `desk lint` checks this. Your own words for the
section and the closing line: `threads: {next: Дальше, closed: Закрыта}`.
