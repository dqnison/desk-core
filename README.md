# desk-core

Движок доставки правил ассистенту (Claude Code). Правила — markdown-файлы
в `DESK_HOME`; каждое само объявляет во фронтматтере, **когда** ему быть
в контексте и **что** оно запрещает. Ядро не знает ни одного инструмента, места
работы или трекера: всё это — данные правил. Только stdlib Python 3, хуки без вызова
модели.

## Правило

```yaml
---
tier: action                 # import | session | action | link
when:                        # когда подать (ярус action; session — вдобавок к старту)
  - git: write               # подкоманда git, которая что-то меняет
  - prompt: '\bcommit\b'     # тема реплики — до первого инструмента
guard:                       # что решить до вызова
  - deny: 'Co-Authored-By|Generated with'
    in: commit-message
    why: в коммитах нет атрибуции инструментов
---
# Git
…текст правила…
```

**Ярусы** — крайний срок, к которому правило в контексте:

| ярус | носитель |
| --- | --- |
| `import` | `@`-импорт во входе ассистента (`~/.claude/CLAUDE.md`); единственная гарантия |
| `session` | SessionStart: содержимым, слотами; хаб активного контекста первым |
| `action` | PreToolUse / UserPromptSubmit / PostToolUse по `when:`; приходит вместе с результатом вызова |
| `link` | нет: справочник по ссылке |

Выхлоп хука длиннее ~10 тыс. знаков харнесс прячет в persisted-файл, поэтому подача
идёт порциями до 12 КБ, крупный файл режется по разделам `##` с пометкой «часть k из m».
Файл подаётся раз на сессию (состояние в `$TMPDIR/desk-core/` или `DESK_STATE_DIR`),
`clear`/`compact` состояние сбрасывают, `resume`/`fork` подачу старта не повторяют.
Не влезшее едет очередью со следующим вызовом. Файл, прочитанный целиком (`Read`,
`cat`), считается поданным.

**`when:`** — список вариантов; внутри варианта все ключи должны совпасть:

| ключ | что |
| --- | --- |
| `tool` | glob имени инструмента (`mcp__metrics*`) |
| `command` | regex по Bash-команде (с учётом регистра; `(?i)` — без) |
| `input` | `{поле: regex}` по входу инструмента (`a.b` — вложенное) |
| `paths` | glob по файлам, которые вызов пишет: Edit/Write и запись из Bash (`>`, `tee`, `sed -i`, `cp/mv`, `rm`, скрипты) |
| `skill` | имя скилла |
| `git` | `write` или список подкоманд (`[worktree]`) |
| `desk` | `true` — запись в `DESK_HOME` или команда, идущая в нём |
| `prompt` | regex по реплике пользователя, без учёта регистра; не сочетается с другими ключами |
| `gate` | опция: первый вызов, пока правила нет в контексте, останавливается `deny` с текстом правила; повтор проходит |

Glob: `**` проходит каталоги, `*` — нет, хвостовой `/` — «всё внутри». Относительный
glob сверяется с путём от корня чекаута файла (или от `DESK_HOME`); у правила
репозитория — только с файлами этого репозитория.

**`guard:`** — `deny` | `ask` | `deny-unless` | `ask-unless` + `in:` (`command`,
`commit-message`, `commit-subject`, `content`, `paths`) + `why:`. Для `in: paths`
шаблон — glob, иначе regex. `ask` спрашивается раз на сессию, `deny` — каждый раз;
текст правила едет тем же выхлопом. Команду хук не переписывает. `DESK_NO_DENY=1`
снимает все deny (и шлагбаумы), ask остаются.

## Контексты

`contexts/<место>/context.md` объявляет, когда место активно:

```yaml
match:
  remote: ['git.example.com']   # подстрока `git remote -v` каталога сессии
  path: ['~/work/*']            # или glob каталога сессии
  default: true                 # или «когда ничего не подошло»
```

Сессия внутри `DESK_HOME/contexts/<место>/` — всегда это место. Правила
`contexts/<место>/` действуют только в нём, `contexts/<место>/projects/<репо>/` —
только для этого репозитория (имя — из remote). Второй репозиторий, которого коснулся
вызов, получает свои файлы `session` с этим вызовом. `skip_in: [desk, <место>]`
снимает файл `session` в сессии внутри `DESK_HOME` или в названном месте.

## Расширения

Что данными не выразить — Python-модуль в `DESK_HOME/ext/`:

```python
from desk import ext

@ext.live("no-force-push", "no force push to a shared branch")
def no_force_push(call, session):       # PreToolUse; на PostToolUse — замечанием
    if "push --force" in call.command:
        return ("deny", "desk: push a fix on top instead")

@ext.compliance("no-force-push")
def cases(record, home):                # desk measure: [(ok, что)]
    ...
```

Есть ещё `ext.lint`. Сломанное расширение не ломает хук: ошибка приходит строкой.
Встроенные проверки ядра сделаны тем же API (`desk/checks.py`): имена мест
(`contexts/<место>/place-words.txt`) в переносимом слое и чтение не влезших в слоты
файлов до первого действия.

## Вход `desk`

```
desk lint                 структура: фронтматтер, when/guard, бюджет, ссылки, контексты, импорты
desk why '<команда>'      сухой прогон хуков: что сработает, что решится, что подастся
desk why --prompt 'текст'   то же для реплики;  --tool Edit <путь> --content '…'
desk measure [--days N]   по транскриптам: вес ярусов, досягаемость, соблюдение
desk init [каталог]       скелет DESK_HOME из template/ и шаги подключения
desk init --hooks         блок хуков для settings.json (если не плагином)
desk doctor               окружение
```

Соблюдение меряется результатом: у каждого `guard:` проверка выводится из его же
описания (deny — «вызов не нарушал», ask — «правило было в контексте до вызова»),
у расширений — своя `compliance`. `since:` в guard — дата, с которой мерить.

## Установка

1. `bin/desk init ~/desk` — скелет `DESK_HOME` (или подключить существующий).
2. `export DESK_HOME=~/desk`.
3. Хуки — плагином: `claude --plugin-dir /path/to/desk-core` на сессию или установка
   из маркетплейса; запасной путь — `desk init --hooks` в `settings.json`.
4. Файлы `tier: import` — `@`-импортами в `~/.claude/CLAUDE.md` (печатает `desk init`);
   `desk.entry` во фронтматтере `desk.md` включает сверку импортов в `desk lint`.

Служебные сессии (`claude -p` из своих инструментов) — `DESK_HOOKS_OFF=1`.

## Раскладка

```
.claude-plugin/plugin.json   манифест плагина
hooks/hooks.json             регистрация: SessionStart ×3 слота, UserPromptSubmit, PreToolUse, PostToolUse
bin/desk, bin/desk-hook      входы CLI и хука
desk/                        пакет: frontmatter, home, when, deliver, guards, ext, checks,
                             hook, transcripts, measure, lint, messages, cli
template/                    скелет DESK_HOME
tests/                       python3 -m unittest
docs/decisions.md            что проверено и почему так
```
