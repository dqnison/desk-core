---
tier: action
when:
  - git: write
  - prompt: '(?<![а-яё])коммит|\bcommit\b'
guard:
  - deny: 'Co-Authored-By|Generated with|🤖'
    in: commit-message
    why: в коммитах нет атрибуции инструментов (Co-Authored-By, «Generated with», 🤖) — убрать футер и повторить
  - deny-unless: '^(Merge |Revert |fixup! |squash! |amend! )|^(?=.{1,72}$)(feat|fix|chore|refactor|docs|test|ci|perf|style|build)\(([A-Z][A-Z0-9]*-\d+|no-ref)\)!?: [a-z0-9`].*[^.\s]$'
    in: commit-subject
    why: subject коммита — `<type>(<TICKET>|no-ref): <description>`, английский, lowercase, без точки, до 72 символов
---

# Git

## Ветки

`<category>/<ticket-lowercase>/<short-kebab-description>`; без тикета — `no-ref`.
Категории: `feature`, `bugfix`, `refactor`, `chore`, `hotfix`, `test`.

## Коммиты

`<type>(<TICKET>): <description>` — например `fix(DEMO-123): handle empty cart`.

- `type`: feat | fix | chore | refactor | docs | test | ci | perf | style | build;
- описание: английский, lowercase, императив, без точки, subject ≤ 72 символов;
- **запрещены** футеры атрибуции инструментов — хук отклоняет такой коммит.
