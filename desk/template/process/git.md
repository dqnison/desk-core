---
tier: action
when:
  - git: write
  - prompt: '\bcommit\b|(?<![а-яё])коммит'
guard:
  - deny: 'Co-Authored-By|Generated with|🤖'
    in: commit-message
    id: commit-no-attribution
    why: no tool attribution in commits (Co-Authored-By, «Generated with», 🤖) — drop the footer and retry
  - deny-unless: '^(Merge |Revert |fixup! |squash! |amend! )|^(?=.{1,72}$)(feat|fix|chore|refactor|docs|test|ci|perf|style|build)(\([^)\s]+\))?!?: [a-z0-9`].*[^.\s]$'
    in: commit-subject
    id: commit-format
    why: commit subject — Conventional Commits `<type>(<scope>): <description>`, lowercase, no trailing dot, ≤ 72 chars
---

# Git

An example policy — edit it to yours; the guards above are what the hook enforces.

## Commits

`<type>(<scope>): <description>` — e.g. `fix(cart): handle an empty cart`.

- `type`: feat | fix | chore | refactor | docs | test | ci | perf | style | build;
- description: imperative, lowercase, no trailing dot, subject ≤ 72 characters;
- no tool attribution footers — the hook denies such a commit.
