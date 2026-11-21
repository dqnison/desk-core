[← Руководство](index.md)

# Подключить насовсем

Репозиторий — [github.com/dqnison/desk-core](https://github.com/dqnison/desk-core).

**CLI** — любым способом:

```bash
uv tool install git+https://github.com/dqnison/desk-core     # или pipx install git+https://…
```

```bash
uvx --from git+https://github.com/dqnison/desk-core desk init ~/desk   # без установки
```

Либо из клона: `~/desk-core/bin/desk`.

1. **Хуки.** Плагином — репозиторий сам себе маркетплейс (`.claude-plugin/marketplace.json`):

   ```bash
   claude plugin marketplace add dqnison/desk-core
   ```

   ```bash
   claude plugin install desk@desk-core
   ```

   На одну сессию из клона — `claude --plugin-dir ~/desk-core`. Либо без плагина: `desk init --hooks`
   печатает блок `hooks` для `~/.claude/settings.json`, `--worktree` добавляет хук
   `WorktreeCreate` и `WorktreeRemove` (см. «Дополнения»). Запускать из чекаута или постоянной установки
   (`uv tool`/`pipx`): под `uvx` в блок попадает путь в кэш uv.
2. **`DESK_HOME`** — в `~/.claude/settings.json`, ключ `env`: `{"DESK_HOME": "/Users/<you>/desk"}`.
   Профиль шелла не годится для десктоп-приложения: оно его не читает.
3. **Ярус `import`** — правила, нужные в каждой сессии: строки `@…`, которые напечатал
   `desk init`, — в `~/.claude/CLAUDE.md`. Их читает сам Claude Code, хук тут ни при чём;
   как это устроено и как не забыть строку для нового правила —
   [rules.md](rules.md#как-ярус-import-попадает-к-модели).
4. **Проверка** — `desk doctor`: Python, `DESK_HOME`, правила, контекст текущего каталога,
   расширения и подключены ли хуки (плагин или `settings.json`).

Хуки — одним способом: плагин и блок в `settings.json` вместе подают каждое правило дважды.

**Переезд с CLAUDE.md.** `desk import CLAUDE.md` раскладывает файл (CLAUDE.md, AGENTS.md,
`.cursorrules`) по разделам `##` в правила `process/<slug>.md` с ярусом `import`; ничего
не перезаписывает. С `@`-строками на них модель видит то же, что раньше. Дальше разделам
дают свой `when:` и ярус `action` — они приезжают к действию, а не висят в каждой сессии.
