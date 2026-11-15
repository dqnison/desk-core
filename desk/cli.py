"""`desk` — one entry: lint, measure, why, init, doctor."""
import argparse
import json
import re
import shutil
import sys
from pathlib import Path

from . import deliver as dl, ext, guards, hook, home as homelib, lint, measure, when as wh

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "template"
SLOTS = 3


def cmd_lint(a):
    problems = lint.run(a.home)
    if problems:
        print(lint.format_problems(problems))
        print(f"\n{len(problems)} проблем(ы)")
        return 1
    print("lint: чисто")
    return 0


def cmd_measure(a):
    print(measure.report(a.home or homelib.desk_home(), a.projects, a.days, a.examples))
    return 0


def _call(a):
    cwd = str(Path(a.cwd or ".").resolve())
    if a.prompt is not None:
        return wh.Call(prompt=a.prompt, cwd=cwd), {"cwd": cwd, "hook_event_name": "UserPromptSubmit",
                                                   "prompt": a.prompt}
    if a.tool in wh.EDIT_TOOLS:
        p = Path(a.target).expanduser()
        inp = {"file_path": str(p if p.is_absolute() else Path(cwd) / p), "content": a.content or ""}
        if a.tool == "Edit":
            inp = {"file_path": inp["file_path"], "new_string": a.content or ""}
    elif a.tool == "Bash":
        inp = {"command": a.target}
    else:
        inp = json.loads(a.input or "{}")
    return wh.Call(tool=a.tool, input=inp, cwd=cwd), {"cwd": cwd, "tool_name": a.tool, "tool_input": inp}


def cmd_why(a):
    """Dry run of the hooks on one call: what fires, what is decided, what is delivered.
    The decisions come from the hook code itself on a throwaway state."""
    home = Path(a.home or homelib.desk_home())
    call, data = _call(a)
    session = homelib.session_from(data, home, project_dir="")
    all_rules = homelib.load_rules(home)
    good = [r for r in all_rules if not r.error]
    repos = {session.repo} if call.is_prompt else wh.repos_touched(call, session)
    scoped = [r for r in good if homelib.in_scope(r, session, repos)]
    print(f"DESK_HOME: {home}")
    print(f"Контекст: {session.context_name or '—'} ({session.basis}); репозиторий: {session.repo or '—'}"
          + (f"; затронуты: {', '.join(sorted(repos))}" if repos - {session.repo} else ""))
    what = f"реплика «{call.prompt}»" if call.is_prompt else f"{call.tool} {json.dumps(call.input, ensure_ascii=False)[:120]}"
    print(f"Вызов: {what} (cwd {call.cwd})")
    if call.targets:
        print("Пишет: " + ", ".join(call.targets))
    broken = [r.rel for r in all_rules if r.error]
    if broken:
        print("Не разобраны (lint): " + ", ".join(broken))

    fired = dl.order_action(wh.fired(hook.deliverable(good, session, repos), call, home, session.context_name))
    print("\nСработают правила:" if fired else "\nНи одно правило не сработало.")
    for f in fired:
        gate = "  [gate]" if f.gated else ""
        print(f"  {f.rule.rel} [{f.rule.tier}] ← {f.why}{gate}")
    found = [] if call.is_prompt else guards.evaluate(scoped, call, session)
    if found:
        print("\nЗапреты:")
        for d in found:
            print(f"  {d.kind:4} {d.id} — {d.reason.removeprefix('desk: ')}")
    for name, err in ext.load(home):
        print(f"  ! расширение {name}: {err}")

    state = dl.State("")          # no session id: nothing is saved
    if call.is_prompt:
        out = hook._deliver(fired, [], state, "UserPromptSubmit")
    else:
        newcomers = hook.newcomer_rules(good, session, repos)
        out = hook._pre_tool({}, session, call, scoped, fired, newcomers, good, state)
    spec = (json.loads(out).get("hookSpecificOutput") or {}) if out else {}
    verdict = spec.get("permissionDecision") or ("подача" if spec.get("additionalContext") else "ничего")
    print(f"\nИтог хука (первый вызов в сессии): {verdict}")
    labels = [ln[4:-4] for ln in (spec.get("additionalContext") or "").splitlines()
              if ln.startswith("--- ") and ln.endswith(" ---")]
    if labels:
        print("  подано: " + ", ".join(labels))
    if state.pending:
        print("  в очередь на следующий вызов: " + ", ".join(state.pending))
    return 0


def registered_slots(settings=None):
    """File slots of SessionStart in a Claude Code settings.json (`--slot N`, N ≥ 1;
    slot 0 is the header); not registered — 1."""
    try:
        hooks = json.loads(Path(settings or Path.home() / ".claude" / "settings.json").read_text())["hooks"]
        cmds = [h["command"] for m in hooks.get("SessionStart", []) for h in m.get("hooks", [])]
        return sum(1 for c in cmds if re.search(r"--slot [1-9]", c)) or 1
    except Exception:
        return 1


def hooks_block():
    entry = f'python3 "{ROOT / "bin" / "desk-hook"}"'
    start = [{"type": "command", "command": f"{entry} --slot {i} --slots {SLOTS}", "timeout": 30}
             for i in range(0, SLOTS + 1)]
    one = [{"type": "command", "command": entry, "timeout": 15}]
    return {"hooks": {
        "SessionStart": [{"matcher": "startup|clear|compact", "hooks": start}],
        "UserPromptSubmit": [{"hooks": one}],
        "PreToolUse": [{"matcher": "*", "hooks": one}],
        "PostToolUse": [{"matcher": "*", "hooks": one}],
        "SessionEnd": [{"hooks": one}],
    }}


def cmd_init(a):
    if a.hooks:
        print(json.dumps(hooks_block(), indent=2, ensure_ascii=False))
        return 0
    target = Path(a.dir or homelib.desk_home()).expanduser()
    if target.exists() and any(target.iterdir()):
        print(f"{target} не пуст — шаблон не копируется. Подключение к существующему DESK_HOME:")
    else:
        shutil.copytree(TEMPLATE, target, dirs_exist_ok=True)
        print(f"Скелет DESK_HOME создан: {target}")
    imports = [r for r in homelib.load_rules(target) if r.tier == "import"]
    print(f"""
1. Переменная окружения (shell profile):
   export DESK_HOME={target}

2. Хуки — плагином Claude Code (на сессию: claude --plugin-dir {ROOT}),
   либо блоком в settings.json: desk init --hooks

3. Ярус import — @-импорты во входе ассистента (~/.claude/CLAUDE.md):""")
    for r in imports:
        print(f"   @{r.path}")
    print("\n4. Проверка: desk lint && desk why 'git commit -m \"fix(no-ref): try it\"'")
    return 0


def cmd_doctor(a):
    home = Path(a.home or homelib.desk_home())
    ok = True
    print(f"python {sys.version.split()[0]}; DESK_HOME {home} — {'есть' if home.is_dir() else 'НЕТ'}")
    if not home.is_dir():
        return 1
    rules = homelib.load_rules(home)
    tiers = {t: sum(1 for r in rules if r.tier == t) for t in homelib.TIERS}
    print("правил: " + ", ".join(f"{t} {n}" for t, n in tiers.items()))
    broken = [r.rel for r in rules if r.error]
    if broken:
        ok = False
        print("не разобраны: " + ", ".join(broken))
    s = homelib.session_from({"cwd": str(Path(".").resolve())}, home, project_dir="")
    print(f"контекст здесь: {s.context_name or '—'} ({s.basis})")
    errs = ext.load(home)
    print(f"расширений с ошибкой: {len(errs)}" + "".join(f"\n  {n}: {e}" for n, e in errs))
    if homelib.quiet():
        print("DESK_HOOKS_OFF задан: хуки молчат")
    return 0 if ok and not errs else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="desk", description="Движок доставки правил ассистенту.")
    ap.add_argument("--home", help="DESK_HOME (по умолчанию переменная окружения или ~/desk)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("lint", help="структура DESK_HOME")
    m = sub.add_parser("measure", help="досягаемость и соблюдение по транскриптам")
    m.add_argument("--days", type=int, default=28)
    m.add_argument("--projects", help="каталог транскриптов (по умолчанию ~/.claude/projects)")
    m.add_argument("--examples", type=int, default=3)
    w = sub.add_parser("why", help="что сработает на вызов (сухой прогон хуков)")
    w.add_argument("target", nargs="?", default="", help="Bash-команда или путь (для --tool Edit/Write)")
    w.add_argument("--tool", default="Bash")
    w.add_argument("--content", help="новый текст для Edit/Write")
    w.add_argument("--input", help="JSON входа для прочих инструментов")
    w.add_argument("--prompt", help="реплика пользователя вместо вызова")
    w.add_argument("--cwd")
    i = sub.add_parser("init", help="скелет DESK_HOME и подключение")
    i.add_argument("dir", nargs="?")
    i.add_argument("--hooks", action="store_true", help="напечатать блок хуков для settings.json")
    sub.add_parser("doctor", help="проверка окружения")
    a = ap.parse_args(argv)
    return {"lint": cmd_lint, "measure": cmd_measure, "why": cmd_why, "init": cmd_init,
            "doctor": cmd_doctor}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
