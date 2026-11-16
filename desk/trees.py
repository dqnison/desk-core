"""Worktrees and docker compose stacks of the machine: what can go, what holds work.

A verdict per worktree, the first wins: `держит работу` — uncommitted, unpushed, or a
branch with no upstream that is not in the base (the only copy is here); `в работе` —
an `@ext.in_work` resolver says its task is in work (none knows — the tree counts as
in work); `свежая работа` — touched within TREE_FRESH_H or a live process works in it;
`сносимо` — the rest. A stack is a compose project tied to a checkout or a tree by its
working dir or env; stacks and volumes with no tree are orphans.

At SessionStart: the sweep — the session repository's trees in detail, what can go in
every checkout by name (the session's plus `@ext.checkouts` providers), stacks. At
PreToolUse: a command working in another known checkout of the same place gets a
one-time hint to move the session there.
"""
import datetime
import json
import os
import re
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

from . import ext, home as homelib
from .checkout import branches, git
from .frontmatter import as_list
from .when import Call, expand_user, expand_vars

SWEEP_MARK = "Деревья и стеки"
SWEEP_EMPTY = "убирать нечего"
TREE_DROP = "сносимо"
TREE_HOLDS = "держит работу"
TREE_LIVE = "в работе"
TREE_FRESH = "свежая работа"
# a touch younger than this outweighs a status: a taken task sits in its status until
# moved by hand, and removing a tree is irreversible
TREE_FRESH_H = 24
# a key is a whole name segment: from the start (or after `/`), digits to its end;
# otherwise an assistant's tree slug (`charming-shirley-5e9f19`) reads as a key
TICKET_RE = re.compile(r"(?:^|/)([A-Z]{2,}-\d+)(?![A-Z0-9])", re.I)


def docker(*args, timeout=3):
    """docker output; None — it did not answer (no binary, daemon silent, timeout).
    "" and None differ on purpose: «no containers» is a fact, «docker silent» is a gap
    to say out loud."""
    try:
        r = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def ticket_key(*parts):
    """Ключ задачи из имени ветки или каталога дерева; нет — `None`."""
    for p in parts:
        m = TICKET_RE.search(p or "")
        if m:
            return m.group(1).upper()
    return None


def tree_verdict(dirty, unpushed, has_upstream, merged, in_work, fresh=False):
    """Вердикт по дереву — ревизия деревьев в начале сессии.

    Порядок жёсткий: `держит работу` (единственная копия здесь) сильнее всего
    остального, дальше `в работе` по статусу задачи, `свежая работа` (`fresh`:
    дерево тронуто недавно или в нём живой процесс — статус мог отстать от дела),
    и только потом `сносимо`.
    Мерж и судьба remote-ветки в критерий не входят: запушенной ветки достаточно,
    чтобы дерево восстановилось, а влитые ветки хостинг обычно не удаляет —
    критерий на их исчезновении не срабатывал никогда.
    """
    if dirty or (has_upstream and unpushed) or (not has_upstream and not merged):
        return TREE_HOLDS
    if in_work is not False:
        return TREE_LIVE
    return TREE_FRESH if fresh else TREE_DROP


def tree_touched(path):
    """Последнее касание дерева, unix-время; `None` — git не ответил.

    Позднее из двух: запись reflog его HEAD (коммит, checkout, pull, reset — всё, что
    двигало HEAD именно в этом дереве, включая его создание до первого коммита)
    и время коммита HEAD. Время записи берётся из самого reflog, а не по mtime файла:
    `git gc` переписывает файлы reflog, и mtime сделал бы свежими все деревья разом.
    """
    row = git(path, "log", "-g", "-1", "--date=unix", "--format=%gd %ct", "HEAD")
    stamps = [int(x) for x in re.findall(r"\d+", row)]
    if not stamps:
        stamps = [int(x) for x in re.findall(r"\d+", git(path, "log", "-1", "--format=%ct"))]
    return max(stamps) if stamps else None


def cwd_processes():
    """Процессы машины с рабочим каталогом: [SimpleNamespace(pid, cmd, cwd)].

    Один вызов `lsof` на весь свод (доли секунды). Не ответил — пустой список: это
    свидетельство только добавляет жизнь, а главное — касание дерева в git — от него
    не зависит. Основной чекаут сюда не смотрит: в нём живут терминалы редактора,
    и любой из них держал бы все деревья репозитория.
    """
    try:
        r = subprocess.run(["lsof", "-n", "-P", "-d", "cwd", "-Fpcn"],
                           capture_output=True, text=True, timeout=5)
    except Exception:
        return []
    out, pid, cmd = [], None, ""
    for row in r.stdout.splitlines():
        tag, val = row[:1], row[1:]
        if tag == "p":
            pid, cmd = int(val) if val.isdigit() else None, ""
        elif tag == "c":
            cmd = val
        elif tag == "n" and pid is not None and val.startswith("/"):
            out.append(SimpleNamespace(pid=pid, cmd=cmd, cwd=Path(val)))
    return out


def procs_in(path, procs):
    """Команды процессов, чей рабочий каталог — дерево `path` или внутри него.
    Путь процесса не разрешается: lsof и так отдаёт настоящий."""
    root = Path(path).resolve()
    return sorted({p.cmd or str(p.pid) for p in procs if Path(p.cwd).is_relative_to(root)})


def main_worktree(cwd):
    """Главная рабочая копия репозитория, из какого бы его дерева ни спросили:
    `--git-common-dir` из дерева ведёт в её `.git`. `None` — не чекаут."""
    common = git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if common and Path(common).name == ".git":
        return Path(common).parent.resolve()
    top = git(cwd, "rev-parse", "--show-toplevel")
    return Path(top).resolve() if top else None


def trees_state(cwd, integration, production, in_work_of=None, procs=None, now=None):
    """Деревья репозитория с вердиктом на каждое; главная рабочая копия — не дерево.

    Вердикты, первый выигрывает: `держит работу` — незакоммиченное, незапушенное или
    ветка без upstream, которой нет в базе; `в работе` — задача дерева числится в работе
    (снимок несвежий — дерево с тикетом тоже `в работе`); `свежая работа` — дерево задачи
    тронуто за сутки или в нём рабочий каталог живого процесса; `сносимо` — остальное
    (ветка без тикета — в пакет с пометкой).

    `in_work_of(branch, slug)` — «идёт ли по дереву работа» (`ext.in_work_of`);
    без него статус неизвестен, и ни одно дерево не станет `сносимо`.

    Свежая работа — `tree_touched` моложе `TREE_FRESH_H` или процесс с рабочим
    каталогом в дереве (`procs` из `cwd_processes`; не передан — спрашивается здесь,
    поэтому свод по многим чекаутам передаёт один список на всех). `now` — для тестов.
    Свежесть по задаче — на другие её деревья — досчитывает `spread_fresh` поверх всех
    чекаутов: вызывающий обязан его позвать.

    Отсеивается именно она, а не `cwd`: сессия внутри дерева видит в
    `worktree list` и главный чекаут, и по влитой ветке с удалённой remote он
    получил бы вердикт «сносимо» — то есть попал бы в пакет уборки.

    Без сети: `origin/*` берутся такими, какими их оставил последний fetch —
    возраст базы уже сообщает checkout_lines.
    """
    out, main = [], main_worktree(cwd)
    procs = cwd_processes() if procs is None else procs
    now = time.time() if now is None else now
    for line in git(cwd, "worktree", "list", "--porcelain").split("\n\n"):
        path = branch = ""
        for row in line.splitlines():
            if row.startswith("worktree "):
                path = row[len("worktree "):]
            elif row.startswith("branch "):
                branch = row[len("branch "):].replace("refs/heads/", "", 1)
        if not path or (main and Path(path).resolve() == main):
            continue
        if not branch:
            # отсоединённая голова: ветки нет, значит нечего сверять с базой —
            # такое дерево показывается, но в пакет уборки не попадает
            sha = git(path, "rev-parse", "--short", "HEAD")
            out.append(SimpleNamespace(
                path=path, slug=Path(path).name, branch=f"отсоединён на {sha}" if sha else "отсоединён",
                dirty=len(git(path, "status", "--porcelain", "-uall").splitlines()),
                unpushed=0, has_upstream=False, merged=False, remote_gone=False,
                key=None, in_work=None, touched=tree_touched(path), procs=procs_in(path, procs),
                fresh=False, fresh_via=None, verdict=TREE_HOLDS))
            continue
        has_upstream = bool(git(path, "rev-parse", "--abbrev-ref", "--quiet", "@{u}"))
        merged = any(
            base and git(path, "rev-parse", "--verify", "--quiet", f"origin/{base}")
            and subprocess.run(["git", "-C", path, "merge-base", "--is-ancestor",
                                branch, f"origin/{base}"], capture_output=True).returncode == 0
            for base in (integration, production))
        dirty = len(git(path, "status", "--porcelain", "-uall").splitlines())
        unpushed = int(git(path, "rev-list", "--count", "@{u}..HEAD") or 0) if has_upstream else 0
        remote_gone = not git(path, "rev-parse", "--verify", "--quiet",
                               f"refs/remotes/origin/{branch}")
        slug = Path(path).name
        in_work = in_work_of(branch, slug) if in_work_of else None
        touched, busy = tree_touched(path), procs_in(path, procs)
        fresh = bool(busy) or (touched is not None and now - touched < TREE_FRESH_H * 3600)
        out.append(SimpleNamespace(
            path=path, slug=slug, branch=branch, dirty=dirty, unpushed=unpushed,
            has_upstream=has_upstream, merged=merged, remote_gone=remote_gone,
            key=ticket_key(branch, slug), in_work=in_work, touched=touched, procs=busy,
            fresh=fresh, fresh_via=None,
            verdict=tree_verdict(dirty, unpushed, has_upstream, merged, in_work, fresh)))
    return out


def spread_fresh(trees):
    """Свежесть — свойство задачи, а не только дерева: `сносимо` стоит на том, что
    задача ждёт чужого хода, а свежая работа в любом её дереве это опровергает.
    Сносимое дерево задачи, свежей в другом дереве, становится `свежая работа`,
    `fresh_via` — слаг того дерева. Деревья — со всех чекаутов разом: задача бывает
    шире репозитория. Возвращает тот же список."""
    live = {}
    for t in trees:
        if t.key and t.fresh:
            live.setdefault(t.key, t.slug)
    for t in trees:
        if t.verdict == TREE_DROP and t.key in live:
            t.verdict, t.fresh_via = TREE_FRESH, live[t.key]
    return trees


def compose_projects(root):
    """Имена compose-проектов, объявленные файлами чекаута или дерева.

    Volume метки рабочего каталога не несёт, поэтому связать его с деревом можно
    только через объявленное имя: `COMPOSE_PROJECT_NAME` в любом `.env*` или
    `name:` верхним уровнем compose-файла, глубина до двух каталогов.

    Полным этот ответ не бывает: имя задаётся ещё и флагом `-p` из sh-обёртки, а её
    не разобрать. Прямой `docker compose config` тут не помогает — он тоже не видит
    `-p` и на репозитории без объявленного имени отвечает базовым именем каталога,
    то есть подставляет выдуманное; спросить можно только **саму обёртку**
    (`apps/workplace/bin/sweep.py`, `wrapper_project`), и это делается перед
    необратимым `down -v`, а не при инвентаризации. Здесь неполнота лечится тем,
    что незнание считается жизнью, — см. `parse_stacks`.
    """
    out, root = set(), Path(root)
    for pat in (".env*", "*/.env*", "*/*/.env*"):
        for f in root.glob(pat):
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for m in re.finditer(r"^\s*COMPOSE_PROJECT_NAME\s*=\s*(\S+)", text, re.M):
                out.add(m.group(1).strip("'\""))
    for pat in ("docker-compose*.y*ml", "*/docker-compose*.y*ml", "*/*/docker-compose*.y*ml"):
        for f in root.glob(pat):
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for m in re.finditer(r"^name:\s*(\S+)", text, re.M):
                out.add(m.group(1).strip("'\""))
    return out


def is_kin(project, live_dir):
    """Принадлежит ли compose-проект каталогу живой рабочей копии.

    Обёртки, которые выводят имя проекта из каталога, дают `<общее>-<каталог>` —
    отсюда точный суффикс. Плюс два края: имя, равное каталогу, и имя короче
    каталога — у главной копии оно задаётся дефолтом обёртки (`shop` при каталоге
    `shop-api`), и без этой ветки погашенный стек главной копии отдал бы свою
    локальную базу в сироты.

    Имя, заданное руками и не выводимое из каталога, сюда не попадает — его ловит
    `compose_projects` по объявлению в файлах копии.
    """
    if not project or not live_dir:
        return False
    return (project == live_dir or project.endswith(f"-{live_dir}")
            or live_dir.startswith(f"{project}-"))


def parse_stacks(ps_out, volumes_out, live_projects, live_dirs=(), exists=None):
    """Разбор вывода docker в стеки: поднятые, осиротевшие и наборы volume без дерева.

    Признак жизни — объединение свидетельств, и каждое умеет только добавлять
    жизнь: контейнер с существующим рабочим каталогом (он же покрывает чекауты
    с именем проекта по умолчанию), объявленное имя из файлов дерева
    (`compose_projects`), родство имени с каталогом живого дерева (`is_kin`).
    Полного знания об именах нет и не будет, поэтому любая неточность промахивается
    в сторону «не трогать»: лишний volume переживает уборку, база живого дерева —
    нет. Обратная сторона названа честно: мёртвый набор, чьё имя роднится с живым
    деревом (`api-platform-de-1439` при живом `de-1439-fix`), в свод не попадёт.
    """
    exists = exists or (lambda p: Path(p).exists())
    live_projects = set(live_projects)
    up, orphan, alive = {}, {}, set(live_projects)
    for row in ps_out.splitlines():
        parts = row.split("\t")
        if len(parts) < 3 or not parts[0]:
            continue
        project, state, wd = parts[0], parts[1], parts[2]
        if wd and exists(wd):
            alive.add(project)
            if state == "running":
                up[project] = wd
        elif wd and project not in alive:
            orphan[project] = wd
    live = lambda p: (p in alive
                      or any(is_kin(p, d) for d in live_dirs))
    vols = sorted({p for _, p in (
        (r.split("\t") + [""])[:2] for r in volumes_out.splitlines()) if p and not live(p)})
    return SimpleNamespace(up=sorted(up), orphan=sorted(k for k in orphan if not live(k)),
                           orphan_volumes=vols)


PS_FORMAT = ('{{.Label "com.docker.compose.project"}}\t{{.State}}'
             '\t{{.Label "com.docker.compose.project.working_dir"}}')
VOLUMES_FORMAT = '{{.Name}}\t{{.Label "com.docker.compose.project"}}'


def stacks_state(*, checkouts_and_trees):
    """Стеки машины: docker спрашивается один раз, метками compose.

    Аргумент — чекауты **и** пути их деревьев, одним списком, и только по имени:
    забыть деревья означает записать их стеки в сироты (у остановленного стека
    контейнеров нет, живым его делает файл окружения дерева), а ответ при этом
    выглядит правдоподобно. Промах должен быть виден в вызове, а не в выдаче.
    """
    ps = docker("ps", "-a", "--format", PS_FORMAT)
    if ps is None:
        return None
    vols = docker("volume", "ls", "--format", VOLUMES_FORMAT) or ""
    declared = (set().union(*(compose_projects(r) for r in checkouts_and_trees))
                if checkouts_and_trees else set())
    return parse_stacks(ps, vols, declared,
                        live_dirs={Path(r).name for r in checkouts_and_trees})


def place_of(home, path, contexts=None):
    """The place of a checkout — by its own remote, not by the session's."""
    ctx, _ = homelib.pick_context(contexts if contexts is not None else homelib.load_contexts(home),
                                  path, homelib.remotes_of(str(path)))
    return ctx


def inventory(home, checkouts, integration="", production="", procs=None):
    """{checkout: ((integration, production), place, trees)} — branches and «in work»
    by each checkout's own place; `integration`/`production` — the fallback when the
    place declares none. Fresh work of a task is spread over all its trees."""
    procs = cwd_processes() if procs is None else procs
    contexts = homelib.load_contexts(home)
    out = {}
    for c in checkouts:
        ctx = place_of(home, c, contexts)
        own = branches(home, ctx.name if ctx else None, homelib.repo_name(homelib.remotes_of(str(c))))
        pair = (own[0] or integration, own[1] or production)
        in_work = (lambda *names, ctx=ctx: ext.in_work_of(names, ctx))
        out[c] = (pair, ctx, trees_state(c, *pair, in_work, procs))
    spread_fresh([t for *_, ts in out.values() for t in ts])
    return out


def checkouts_of(session):
    """Checkouts the sweep covers: the session's main working copy and what
    `@ext.checkouts` providers name."""
    here = main_worktree(session.cwd)
    out = [Path(c).resolve() for c in ext.checkouts_of(session)]
    if here and here not in out:
        out.append(here)
    return here, out


def sweep_lines(home, here, checkouts, integration="", production=""):
    """What can go: the session repository's trees in detail, droppable trees of
    every checkout by name (a removal batch is built from these slugs), stacks."""
    trees = {c: ts for c, (_, _, ts) in inventory(home, checkouts, integration, production).items()}
    stacks = stacks_state(checkouts_and_trees=[*checkouts, *(t.path for ts in trees.values() for t in ts)])
    drop = {c: [t for t in ts if t.verdict == TREE_DROP] for c, ts in trees.items()}
    drop = {c: v for c, v in drop.items() if v}
    out = []
    if here and trees.get(here):
        rows = [f"{t.slug} ({t.branch}) — {t.verdict}" for t in trees[here]]
        out.append(f"[desk] {SWEEP_MARK} — деревья {here.name}: " + "; ".join(rows) + ".")
    for c, v in drop.items():
        why = "запушено, задача ждёт чужого хода" if all(t.key for t in v) else "запушено, статуса у ветки нет"
        out.append(f"Сносимо в {c.name}: {', '.join(t.slug for t in v)} — {why}.")
    if drop:
        out.append("Снос — по просьбе человека; самой сессии уборку не предлагать.")
    if stacks is None:
        out.append("Докер не ответил — про стеки и volume в этом своде ничего не сказано; "
                   "молчание здесь не значит, что убирать нечего.")
    else:
        if len(stacks.up) > 1:
            out.append(f"Поднятых стеков сразу: {len(stacks.up)} — {', '.join(stacks.up)}. "
                       "Столько оправдано параллельной работой; стек копии, к которой "
                       "не возвращаются, гасится.")
        if stacks.orphan:
            out.append(f"Стеки без своего дерева: {', '.join(stacks.orphan)}.")
        if stacks.orphan_volumes:
            out.append("Наборы volume без своего дерева: " + ", ".join(stacks.orphan_volumes) + ".")
    if not out:
        out = [f"[desk] {SWEEP_MARK} — {SWEEP_EMPTY}."]
    elif not out[0].startswith("[desk]"):
        out[0] = f"[desk] {SWEEP_MARK}: {out[0]}"
    return out


@ext.on("SessionStart")
def on_start(ev):
    s, h = ev.session, ev.header
    if homelib.config(s.home).get("sweep") is not True:      # opt-in: `sweep: true` in desk.yml
        return None
    here, checkouts = checkouts_of(s)
    if not checkouts:
        return None
    integration, production = branches(s.home, s.context_name, s.repo)
    out = sweep_lines(s.home, here, checkouts, integration, production)
    bare = [x.removeprefix(f"[desk] {SWEEP_MARK}: ") for x in out]
    drop = [x.split(" — ", 1)[0] for x in bare if x.startswith(("Сносимо в", "Стеки без", "Наборы volume"))]
    if drop and h is not None:
        h.signal("уборка", " · ".join(x[:1].lower() + x[1:] for x in drop), "🧹", "trash")
    return "\n".join(out)


def foreign_checkouts(call, session, checkouts=None):
    """{repo: root} — known checkouts (theirs trees inside, `.claude/worktrees/`, and
    neighbours `<name>-worktrees/`) of the session's place that the command names
    (`cd`, `git -C`, a path), the session's own repository and DESK_HOME aside."""
    if call.tool != "Bash" or homelib.is_inside(call.cwd, session.home):
        return {}
    known = ext.checkouts_of(session) if checkouts is None else checkouts
    known = [Path(c) for c in known if Path(c).name in expand_vars(call.command)]
    if not known:
        return {}
    areas = [a for c in known for a in (c, c.parent / f"{c.name}-worktrees")]
    contexts = homelib.load_contexts(session.home)
    out = {}
    for p in [*call.dirs, *call.named_paths]:
        if not any(Path(p).is_relative_to(a) for a in areas):
            continue
        repo, root = homelib.repo_of_path(p)
        if not repo or repo == session.repo or repo in out or homelib.is_inside(root, session.home):
            continue
        ctx = place_of(session.home, root, contexts)
        if (ctx.name if ctx else None) == session.context_name:
            out[repo] = root
    return out


CD_HINT = "[desk] Работа идёт в {repo}, а сессия — в {cwd}: change_directory в {path}"


@ext.on("PreToolUse")
def cd_hint(ev):
    """A command works in a known checkout of the place that is not the session's: a line
    with change_directory, once per session per repository."""
    found = foreign_checkouts(ev.call, ev.session)
    fresh = {r: p for r, p in sorted(found.items()) if f"cd:{r}" not in ev.state.notes}
    ev.state.notes |= {f"cd:{r}" for r in fresh}
    return "\n".join(CD_HINT.format(repo=r, cwd=ev.call.cwd, path=p) for r, p in fresh.items()) or None
