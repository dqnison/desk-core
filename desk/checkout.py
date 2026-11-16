"""The session's checkout against its base branch: where it stands, bringing an idle
checkout to the base, and whether another live session works in it.

Branches are data of a place: `branches:` in `contexts/<place>/desk.yml` —
`integration`, `production`, and `repos: {<repo>: {integration, production}}` for a
repository that differs. No integration branch known — nothing is said.

At SessionStart: a header row (`чекаут`/`дерево`), the state and the command that
brings the checkout to its base. A checkout that is clean and whose branch is pushed
or merged is brought to the base by the hook itself on startup — the branch stays,
so it is reversible — unless it is busy: another live assistant session works in it,
or an `@ext.in_work` resolver says the branch's task is in work.
"""
import json
import os
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

from . import ext, home as homelib
from .transcripts import PROJECTS
from .when import expand_user

BASE_MARK = "Чекаут репозитория сессии"
BASE_OK, BASE_OFF = "на базе", "не на базе"
TREE_MARK = "Дерево сессии"
BUSY_MARK = "Чекаут занят"
AUTO_MARK = "приведён к базе хуком"
IDLE_MARK = "Работа на ветке не живёт"
TASK_BRANCH = "<категория>/<тикет>/<суть>"
SESSIONS_DIR = Path.home() / ".claude" / "sessions"   # the harness's registry of live sessions
PEER_IDLE_MIN = 120


def git(cwd, *args, timeout=5):
    try:
        r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=timeout)
    except Exception:
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def branches(home, context, repo=""):
    """(integration, production) for a repository of a place: its own entry under
    `repos:` per key, else the place's."""
    raw = homelib.context_config(home, context).get("branches") or {}
    own = ((raw.get("repos") or {}).get(repo) or {}) if repo else {}
    return tuple(str(own.get(k) or raw.get(k) or "") for k in ("integration", "production"))


def checkout_of(session):
    """The checkout the session works in: its cwd when both it and the session folder
    are outside DESK_HOME (the session moved into its worktree), else the folder."""
    inside = homelib.is_inside(session.cwd, session.home) or homelib.is_inside(session.session_dir, session.home)
    return session.session_dir if inside else session.cwd


def state(cwd, integration):
    """Where the checkout stands against `origin/<integration>`, offline (as the last
    fetch left it, hence the base's age). None — not a checkout, or no such base."""
    if not integration or not git(cwd, "rev-parse", "--git-dir"):
        return None
    base = f"origin/{integration}"
    if not git(cwd, "rev-parse", "--verify", "--quiet", base):
        return None
    head = git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
    behind, ahead = ((git(cwd, "rev-list", "--left-right", "--count", f"{base}...HEAD") or "0\t0").split()
                     + ["0", "0"])[:2]
    stamp = git(cwd, "log", "-1", "--format=%ct", base)
    git_dir = git(cwd, "rev-parse", "--path-format=absolute", "--git-dir")
    return SimpleNamespace(
        # a linked worktree: the integration branch is taken by the main checkout
        tree=bool(git_dir) and git_dir != git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir"),
        head=head, integration=integration, base=base, behind=int(behind), ahead=int(ahead),
        dirty=len(git(cwd, "status", "--porcelain", "-uall").splitlines()),
        unpushed=int(git(cwd, "rev-list", "--count", "@{u}..HEAD") or 0)
        if git(cwd, "rev-parse", "--abbrev-ref", "--quiet", "@{u}") else 0,
        base_age_h=int((time.time() - int(stamp)) // 3600) if stamp.isdigit() else None,
        on_base=head == integration and int(behind) == 0)


def placeholder(home):
    """Branch prefix a new session worktree gets before its task is known (`worktree.branch`)."""
    pattern = str((homelib.config(home).get("worktree") or {}).get("branch") or "{name}")
    return pattern.split("{name}", 1)[0]


def tree_lines(st, dirty, age, prefix=""):
    """A session in a linked worktree: the integration branch is the main checkout's,
    so not `checkout <base>` but the worktree's branch to the base and to a task name.
    No own commits or changes — `checkout -B` from the base; there are — rename only."""
    base_ok = st.behind == 0
    head = (f"[desk] {TREE_MARK} — {BASE_OK if base_ok else BASE_OFF}: HEAD = {st.head}"
            + ("" if base_ok else f", позади {st.base} на {st.behind}")
            + (f", впереди на {st.ahead}" if st.ahead else "") + f"{dirty}{age}.")
    if base_ok and not (prefix and st.head.startswith(prefix)):
        return [head]
    cmd = (f"git fetch origin {st.integration} && git checkout -B {TASK_BRANCH} {st.base}"
           if not base_ok and not st.ahead and not st.dirty else f"git branch -m {TASK_BRANCH}")
    return [head, "Основной чекаут не трогается. Ветка дерева — к базе и к имени задачи, "
                  "как только задача известна:", f"    {cmd}"]


def lines(st, busy=(), prefix=""):
    """The fact about the checkout and, off base, the command that brings it there.
    `busy` — why another work lives in it: then no command, a task with code goes
    into its own worktree."""
    if st is None:
        return []
    age = f", origin/{st.integration} снят {st.base_age_h} ч назад" if st.base_age_h is not None else ""
    dirty = f", незакоммичено файлов: {st.dirty}" if st.dirty else ""
    busy_line = (f"[desk] {BUSY_MARK}: {'; '.join(busy)}. Задача с кодом — сразу в своё дерево "
                 f"от свежей origin/{st.integration}, а не переключением здесь; разовое (мерж, "
                 "однострочник) — можно и здесь, не задевая чужую ветку и незакоммиченное.") if busy else ""
    if st.tree:
        return tree_lines(st, dirty, age, prefix)
    if st.on_base:
        return [f"[desk] {BASE_MARK} — {BASE_OK}: HEAD = {st.head}{dirty}{age}.", *([busy_line] if busy else [])]
    where = (f"HEAD = {st.head}" + (f" (не интеграционная {st.integration})" if st.head != st.integration else "")
             + (f", позади {st.base} на {st.behind}" if st.behind else "")
             + (f", впереди на {st.ahead}" if st.ahead else ""))
    if busy:
        return [f"[desk] {BASE_MARK} — {BASE_OFF}: {where}{dirty}{age}.", busy_line]
    out = [f"[desk] {BASE_MARK} — {BASE_OFF}: {where}{dirty}{age}.",
           "База задачи — свежая интеграционная ветка: до разбора постановки, поиска по коду "
           "и тестов либо привести чекаут, либо назвать вслух дерево задачи. Команда:",
           f"    git fetch origin {st.integration} && git checkout {st.integration} "
           f"&& git pull --ff-only origin {st.integration}"]
    if not st.dirty and not st.unpushed:
        out.append(f"{IDLE_MARK}: чисто и всё запушено — команда выполняется сразу, "
                   "не дожидаясь задачи; ветка остаётся, возврат обратим.")
    return out


def auto_on(home):
    """The hook moves an idle checkout to its base only with `checkout: {auto: true}`:
    without it the state and the command are said, git is not touched."""
    return ((homelib.config(home).get("checkout") or {}).get("auto")) is True


def bring_to_base(cwd, st):
    """An idle checkout to its base: only when the work surely lives elsewhere — clean,
    HEAD a branch, pushed whole or merged into the base. The branch stays. Busy is
    the caller's check. A line of fact, or None when untouched."""
    if st is None or st.on_base or st.dirty or st.head in ("", "HEAD") or st.tree:
        return None
    tracked = bool(git(cwd, "rev-parse", "--abbrev-ref", "--quiet", "@{u}"))
    merged = subprocess.run(["git", "-C", str(cwd), "merge-base", "--is-ancestor", "HEAD", st.base],
                            capture_output=True).returncode == 0
    if not ((tracked and not st.unpushed) or merged):
        return None
    git(cwd, "fetch", "-q", "origin", st.integration, timeout=8)    # without it the base is just older
    was = st.head
    for args in (("checkout", "-q", st.integration), ("merge", "-q", "--ff-only", st.base)):
        r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            return f"[desk] Чекаут в простое к базе не приведён: git {' '.join(args)} — {(r.stderr or r.stdout).strip()[:160]}"
    return (f"[desk] Чекаут {AUTO_MARK}: был {was} (чисто, {'влита в базу' if merged else 'запушена'}; "
            f"ветка осталась), теперь {st.integration} = {st.base}.")


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _nested(p, top):
    """`p` is its own working copy inside `top` (a worktree, a submodule, a clone), or gone."""
    return not p.exists() or any((d / ".git").exists() for d in (p, *p.parents) if d != top and d.is_relative_to(top))


def peer_sessions(cwd, own_id, sessions_dir=None, transcripts_dir=None, idle_min=PEER_IDLE_MIN,
                  alive=_alive, now=None):
    """Other live sessions in the same working copy: [(sid, minutes since its last record)].
    A session in a subfolder is the same copy, one in a worktree inside it is not.
    No transcript found — minutes None, and it counts as alive."""
    top = expand_user(git(cwd, "rev-parse", "--show-toplevel") or cwd).resolve()
    now = now if now is not None else time.time()
    out = []
    for f in sorted(Path(sessions_dir or SESSIONS_DIR).glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            pid = int(d.get("pid") or f.stem)
        except (OSError, ValueError):
            continue
        sid, scwd = d.get("sessionId") or "", d.get("cwd") or ""
        if not sid or sid == own_id or not scwd or not alive(pid):
            continue
        p = expand_user(scwd).resolve()
        if p != top and (not p.is_relative_to(top) or _nested(p, top)):
            continue
        logs = list(Path(transcripts_dir or PROJECTS).glob(f"*/{sid}.jsonl"))
        minutes = int((now - max(x.stat().st_mtime for x in logs)) // 60) if logs else None
        if minutes is None or minutes <= idle_min:
            out.append(SimpleNamespace(sid=sid, minutes=minutes))
    return out


def busy(cwd, session, st, **kw):
    """Why the checkout is someone else's: a live peer session, a branch whose task is in work."""
    reasons = []
    peers = peer_sessions(cwd, session.session_id, **kw)
    if peers:
        reasons.append("в нём работает другая сессия ассистента — " + ", ".join(
            f"{p.sid[:8]} ({'ход неизвестен' if p.minutes is None else f'ход {p.minutes} мин назад'})" for p in peers))
    if st is not None and st.head != st.integration and ext.in_work_of((st.head,), session.context) is True:
        reasons.append(f"ветка {st.head} — задача в работе")
    return reasons


@ext.on("SessionStart")
def on_start(ev):
    s, h = ev.session, ev.header
    if s.in_home or s.context is None:
        return None
    integration, _ = branches(s.home, s.context_name, s.repo)
    if not integration:
        return None
    cwd = checkout_of(s)
    st = state(cwd, integration)
    if st is None:
        if h is not None and git(cwd, "rev-parse", "--git-dir"):
            h.row("чекаут", f"origin/{integration} не найден · состояние не считано", "🌿", "git-branch")
        return None
    why = busy(cwd, s, st)
    auto = (bring_to_base(cwd, st) if auto_on(s.home) and not why and ev.data.get("source") == "startup"
            else None)
    if auto and AUTO_MARK in auto:
        st = state(cwd, integration)
    if h is not None:
        where = "на базе" if (st.behind == 0 if st.tree else st.on_base) else f"отстал{'о' if st.tree else ''} на {st.behind}"
        h.row("дерево" if st.tree else "чекаут",
              f"{st.head} · {where} · {f'{st.dirty} не закоммичено' if st.dirty else 'чисто'} · "
              f"база {st.base_age_h} ч назад", "🌿", "git-branch")
        if auto and AUTO_MARK in auto:
            h.row("к базе", auto.split(": ", 1)[1].rstrip("."), "🌿", "git-branch")
        if why:
            h.signal("занято", "; ".join(why), "🚧", "barrier-block")
    return "\n".join([*([auto, ""] if auto else []), *lines(st, why, placeholder(s.home))]).strip() or None
