"""Session journal: a line `date · repository · title` per session, written at
SessionEnd; its tail and the share of sessions about the desk itself at start.

On with `journal: <dir>` in desk.yml (relative to DESK_HOME). `meta_goal: <percent>`
adds a header signal when sessions inside DESK_HOME over the last META_DAYS days
exceed the goal — work on the desk crowding out the work it serves.

One file per month, `<dir>/YYYY-MM.md`; a session's line carries its id, so a second
exit of the same session replaces it. Sessions end at once: the write is locked and
atomic. A service session with no user turn is not written.
"""
import contextlib
import datetime
import fcntl
import os
import re
import subprocess
import tempfile
from pathlib import Path

from . import ext, home as homelib
from .transcripts import records

META_DAYS = 7
TAIL = 10
ROW_RE = re.compile(r"^- (\d{4}-\d{2}-\d{2}) [\d:]+ · (\S+) · ")


def journal_dir(home):
    d = homelib.config(home).get("journal")
    return Path(home) / str(d) if d else None


def project_of(cwd):
    """Repository name, not the directory: a session in a worktree goes under its main copy."""
    for args in (["--path-format=absolute", "--git-common-dir"], ["--show-toplevel"]):
        try:
            out = subprocess.run(["git", "-C", str(cwd), "rev-parse", *args], capture_output=True,
                                 text=True, timeout=5).stdout.strip()
        except Exception:
            out = ""
        if out:
            return Path(out).parent.name if out.endswith("/.git") else Path(out).name
    return Path(cwd).name or "?"


def session_title(path):
    """(title, had a user turn): customTitle unless it is a cut of the first prompt,
    else aiTitle, else the first line of the last prompt."""
    custom = ai = prompt = None
    has_user = False
    for rec in records(path) if path and Path(path).exists() else []:
        has_user |= rec.get("type") == "user"
        custom = rec.get("customTitle") or custom
        ai = rec.get("aiTitle") or ai
        prompt = rec.get("lastPrompt") or prompt
    head = (prompt or "").strip().split("\n")[0][:70]
    if custom and not (prompt and prompt.startswith(custom)):
        return custom, has_user
    return ai or custom or head or "(без заголовка)", has_user


def write(jdir, sid, project, title, now=None):
    now = now or datetime.datetime.now()
    f, mark = jdir / f"{now:%Y-%m}.md", f"<!-- {sid} -->"
    jdir.mkdir(parents=True, exist_ok=True)
    with open(jdir / ".lock", "w") as lock:
        with contextlib.suppress(OSError):
            fcntl.flock(lock, fcntl.LOCK_EX)
        lines = f.read_text(encoding="utf-8").splitlines() if f.exists() else [f"# Журнал сессий — {now:%Y-%m}", ""]
        lines = [line for line in lines if mark not in line]
        lines.append(f"- {now:%Y-%m-%d %H:%M} · {project} · {title} {mark}")
        fd, tmp = tempfile.mkstemp(dir=jdir, prefix=f.name, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        os.replace(tmp, f)


def share(jdir, project, days=META_DAYS, today=None):
    """(sessions of `project`, all sessions) over the last `days` days."""
    since = ((today or datetime.date.today()) - datetime.timedelta(days=days)).isoformat()
    mine = total = 0
    for f in sorted(jdir.glob("20*.md")):        # months; other notes in the folder are not sessions
        for ln in f.read_text(encoding="utf-8").splitlines():
            m = ROW_RE.match(ln)
            if m and m.group(1) >= since:
                total += 1
                mine += m.group(2) == project
    return mine, total


def tail(jdir, lines=TAIL):
    f = jdir / (datetime.date.today().strftime("%Y-%m") + ".md")
    return "\n".join(f.read_text(encoding="utf-8").splitlines()[-lines:]) if f.exists() else ""


@ext.on("SessionEnd")
def on_end(ev):
    jdir = journal_dir(ev.session.home)
    if jdir is None:
        return None
    title, has_user = session_title(ev.data.get("transcript_path"))
    sid = (ev.data.get("session_id") or "")[:8]
    if has_user and sid:
        write(jdir, sid, project_of(ev.data.get("cwd") or "."), title)
    return None


@ext.on("SessionStart")
def on_start(ev):
    home = ev.session.home
    jdir = journal_dir(home)
    if jdir is None:
        return None
    goal = homelib.config(home).get("meta_goal")
    if goal is not None and ev.header is not None:
        mine, total = share(jdir, Path(home).name)
        if total and 100 * mine > float(goal) * total:
            ev.header.signal("мета", f"{Path(home).name} {mine} из {total} сессий за {META_DAYS} дн · "
                                     f"{round(100 * mine / total)} % при цели ≤ {goal} %", "🔁", "repeat")
    text = tail(jdir)
    return f"[desk] Хвост журнала сессий ({jdir.relative_to(home)}/):\n{text}" if text else None
