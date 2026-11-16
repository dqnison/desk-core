"""Threads: what lasts longer than a session — an intention or an unfinished
inquiry (template process/threads.md).

A thread is `threads/<slug>.md` at DESK_HOME (about the desk itself) or under
`contexts/<ctx>/threads/`. While open it is linked from the `now.md` next to its
`threads/` folder and has a «Дальше» section (the next step); closed, it leaves
`now.md`, gets `closed: <date>` and a line «**Закрыта <date>.** <decision>».
Frontmatter: `status: open|closed`. Each `now.md` loads at session start, so it
stays small and gets revised.
"""
import datetime
import re
import subprocess
from pathlib import Path

from . import ext, frontmatter, home as homelib

NOW_MAX_BYTES = 4096
STALE_DAYS = 14
NEXT_HEADER = "## Дальше"
CLOSED_MARK = "**Закрыта"
_LINK_RE = re.compile(r"\(([^)#\s]*threads/[^)#]+\.md)\)")


def on(home):
    """The threads checks run with `threads: true` in desk.yml."""
    return homelib.config(home).get("threads") is True


def now_files(home):
    """Thread indexes: the root now.md (threads about the desk), then each context's."""
    home = Path(home)
    return [p for p in [home / "now.md", *sorted(home.glob("contexts/*/now.md"))] if p.exists()]


def thread_files(home):
    home = Path(home)
    return sorted(home.glob("threads/*.md")) + sorted(home.glob("contexts/*/threads/*.md"))


def open_threads(home):
    return [p for p in thread_files(home) if frontmatter.read(p)[0].get("status") == "open"]


def now_of(thread):
    """The index a thread belongs in: the now.md next to its threads/ folder."""
    return Path(thread).parent.parent / "now.md"


@ext.lint("threads", "нити: статус, место в now.md, «Дальше» у открытой, «Закрыта» у закрытой")
def lint_threads(home):
    out, linked = [], {}
    if not on(home):
        return out
    for now in now_files(home):
        for link in _LINK_RE.findall(now.read_text(encoding="utf-8")):
            target = (now.parent / link).resolve()
            if not target.exists():
                out.append((now, 1, f"ссылка на несуществующую нить: {link}"))
            else:
                linked[target] = now
    for p in thread_files(home):
        text = p.read_text(encoding="utf-8")
        try:
            fm = frontmatter.parse(text)
        except frontmatter.FrontmatterError:
            fm = {}
        status, where = fm.get("status"), linked.get(p.resolve())
        if status == "open":
            if "closed" in fm:
                out.append((p, 2, "status: open при заполненном closed:"))
            if where != now_of(p):
                out.append((p, 1, f"открытая нить не упомянута в {now_of(p).relative_to(home)}"))
            if NEXT_HEADER not in text:
                out.append((p, 1, "у открытой нити нет раздела «Дальше» (следующего действия)"))
        elif status == "closed":
            if "closed" not in fm:
                out.append((p, 2, "status: closed без даты closed:"))
            if CLOSED_MARK not in text:
                out.append((p, 1, "закрытая нить без строки «**Закрыта <дата>.**»"))
            if where:
                out.append((where, 1, f"закрытая нить в оглавлении: {p.relative_to(home)}"))
        else:
            out.append((p, 2, f"нет status: open|closed (сейчас: {status!r})"))
    return out


def _git(home, *args):
    try:
        return subprocess.run(["git", "-C", str(home), *args], capture_output=True,
                              text=True, timeout=5).stdout.strip()
    except Exception:
        return ""


@ext.lint("freshness", "now.md не крупнее 4 КБ и ревизуется")
def lint_freshness(home):
    """Each now.md is at most NOW_MAX_BYTES and was touched within STALE_DAYS: a change
    in the working tree counts, otherwise the date of its last commit."""
    out = []
    if not on(home):
        return out
    for now in now_files(home):
        rel = now.relative_to(home)
        size = now.stat().st_size
        if size > NOW_MAX_BYTES:
            out.append((now, 1, f"{rel} {size} байт при лимите {NOW_MAX_BYTES} — переносить в файлы нитей"))
        if _git(home, "status", "--porcelain", "--", str(rel)):
            continue
        last = _git(home, "log", "-1", "--format=%cs", "--", str(rel))
        if last:
            age = (datetime.date.today() - datetime.date.fromisoformat(last)).days
            if age > STALE_DAYS:
                out.append((now, 1, f"{rel} не трогали {age} дн. (порог {STALE_DAYS}) — ревизия нитей"))
    return out
