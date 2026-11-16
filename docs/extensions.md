[← Руководство](index.md)

# Расширения: проверки кодом

Что не выражается regex-ом — Python-файл в `DESK_HOME/ext/`:

```python
from desk import ext

@ext.live("no-force-push", "no force push to a shared branch")
def no_force_push(call, session):          # перед вызовом
    if "push --force" in call.command:
        return ("deny", "desk: push a fix on top instead")

@ext.compliance("no-force-push")
def cases(record, home):                   # для desk measure: [(ok, что)]
    ...
```

Есть ещё `@ext.lint`, `@ext.on(event)` — свой текст на событие, `@ext.in_work` — идёт ли
работа по ветке (`fn(names, context)` → True/False/None, например статус задачи в трекере:
по нему чекаут не приводится к базе, а дерево не сносится) и `@ext.checkouts` — чекауты
машины для свода деревьев и подсказки `change_directory`. Упавшее расширение хук не
ломает — ошибка приходит строкой.
Общий код расширений — пакетом в подпапке (`ext/mylib/`): расширениями грузятся
только `ext/*.py`, а сама `ext/` стоит в `sys.path`, так что `from mylib import x`.
Пример — [desk/template/ext/no_force_push.py](../desk/template/ext/no_force_push.py).

**События.** Для своего поведения, которое не правило, — `@ext.on(событие)`:
`SessionStart` (текст идёт в отдельный выхлоп старта вместе со строкой контекста),
`UserPromptSubmit`, `PreToolUse`, `PostToolUse` (текст идёт модели с выхлопом хука),
`SessionEnd` (журнал; вывода нет). Обработчик получает `ev` с `session`, `call`,
`data` (вход хука), `state` (учёт сессии; `state.notes` — для «сказать один раз»),
`changed` (что команда реально изменила) и на старте — `header`.

**Шапка сессии.** Ядро собирает её само: заголовок «контекст → репозиторий» и строку
«подача» (вес в сессии и за триггерами), рисует рамкой (CLI) и HTML для виджета
(десктоп) с инструкцией модели вывести первым блоком. Расширение добавляет свои
строки и сигналы: `ev.header.row("чекаут", "main · чисто", "🌿", "git-branch")`,
`ev.header.signal("уборка", "…", "🧹", "trash")` — сигналы идут под разделителем
и подсвечены. Включается `header: true` в `desk.yml` (без него `ev.header` — `None`).

```python
@ext.on("PreToolUse")
def hint(ev):
    if "deploy" in ev.call.command and "deploy-hint" not in ev.state.notes:
        ev.state.notes.add("deploy-hint")
        return "[desk] выкат — только через пайплайн"
```
