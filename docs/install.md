[← Руководство](index.md)

# Подключить насовсем

**CLI** — любым способом (`<repo>` — путь к чекауту или `git+<url>`):

```bash
uvx --from <repo> desk init ~/desk     # без установки
```

```bash
uv tool install <repo>                 # или pipx install <repo>
```

Либо прямо из чекаута: `<repo>/bin/desk`.

1. **Хуки.** Плагином из локального каталога (маркетплейс — `.claude-plugin/marketplace.json`):

   ```bash
   claude plugin marketplace add ~/desk-core
   ```

   ```bash
   claude plugin install desk@desk-core
   ```

   На одну сессию — `claude --plugin-dir <repo>`. Либо без плагина: `desk init --hooks`
   печатает блок `hooks` для `~/.claude/settings.json`, `--worktree` добавляет хук
   `WorktreeCreate` (см. «Дополнения»). Запускать из чекаута или постоянной установки
   (`uv tool`/`pipx`): под `uvx` в блок попадает путь в кэш uv.
2. **`DESK_HOME`** — в `~/.claude/settings.json`, ключ `env`: `{"DESK_HOME": "/Users/<you>/desk"}`.
   Профиль шелла не годится для десктоп-приложения: оно его не читает.
3. **Ярус `import`** ([что это](rules.md)) — правила, нужные в каждой сессии. Хук
   подаёт правила сообщением и порциями, а `@`-импорт
   в `~/.claude/CLAUDE.md` Claude Code грузит в каждую сессию сам — это единственная
   гарантия. Нужные
   строки печатает `desk init <каталог>`.
4. **Проверка** — `desk doctor`: Python, `DESK_HOME`, правила, контекст текущего каталога,
   расширения и подключены ли хуки (плагин или `settings.json`).

Хуки — одним способом: плагин и блок в `settings.json` вместе подают каждое правило дважды.

**Переезд с CLAUDE.md.** `desk import CLAUDE.md` раскладывает файл (CLAUDE.md, AGENTS.md,
`.cursorrules`) по разделам `##` в правила `process/<slug>.md` с ярусом `import`; ничего
не перезаписывает. С `@`-строками на них модель видит то же, что раньше. Дальше разделам
дают свой `when:` и ярус `action` — они приезжают к действию, а не висят в каждой сессии.
