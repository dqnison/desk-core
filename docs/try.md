[← Руководство](index.md)

# Как это выглядит

Файл `process/git.md` в `DESK_HOME`:

```markdown
---
tier: action
when:
  - git: write                  # подать, когда модель зовёт git-команду, которая что-то меняет
guard:
  - deny-unless: '^(feat|fix|chore)\((no-ref|[A-Z]+-\d+)\): [a-z]'
    in: commit-subject          # проверять заголовок коммита
    why: subject — <type>(<TICKET>|no-ref): <description>
---
# Git
…текст правила для модели…
```

Модель в сессии вызывает `git commit -m "fixed bug"`. Хук перед вызовом:

1. видит, что сработал `when: git: write` → кладёт текст `git.md` в контекст модели;
2. видит, что заголовок не проходит `guard` → **отклоняет вызов** с причиной из `why`;
3. модель читает причину и правило, повторяет с `fix(no-ref): handle empty cart` — проходит.

Правило подаётся один раз за сессию. Если файл уже прочитан моделью целиком, он
второй раз не подаётся.

## Попробовать (5 минут, ничего не ставя)

```bash
git clone https://github.com/dqnison/desk-core ~/desk-core
```

```bash
alias desk=~/desk-core/bin/desk
```

```bash
desk init ~/desk-try
```

Создаёт скелет `DESK_HOME` из [desk/template/](../desk/template/): правила про git, тесты,
цикл задачи и нити ([что это](concepts.md#дополнения)), стартовый набор запретов `process/safety.md`, пример места работы
`contexts/example/` (Acme, `git.example.com`, репозиторий `shop-api`) и личный контекст.
Правила шаблона — на английском.

```bash
export DESK_HOME=~/desk-try
```

```bash
desk why 'git commit -m "fixed bug"'
```

Сухой прогон: какие правила сработают на эту команду, что решит запрет, что будет подано.
Ничего не выполняет. Вариации: `desk why --prompt 'сделай коммит'` (реплика),
`desk why --tool Edit billing/pay.py --content 'x'` (правка файла), `--cwd <каталог>` —
как будто сессия в другом каталоге, `--exit-code` — код выхода 2 при запрете, 1 при
спросе (для скриптов и CI).

```bash
claude --plugin-dir ~/desk-core
```

Сессия Claude Code с хуками ядра — только на этот запуск, конфиг не меняется. Попроси
закоммитить что-нибудь с сообщением «fixed bug» — вызов будет отклонён.

`DESK_HOME` хук берёт из окружения; без него — `~/desk`.
