"""Universal checks the core ships, registered through the extension API.

Two of the five universal checks of the old binding are plain data now and live in
rule files (see template/): no tool attribution in commits (`deny` in
`commit-message`) and the commit subject format (`deny-unless` in
`commit-subject`). The third — "the rule was read before touching a sensitive
path" — is the derived compliance of any `ask` guard (desk.measure). The two that
need code are here:

- place-words: names of a context (`contexts/<c>/place-words.txt`) do not appear in
  the portable layer of DESK_HOME. Portable = `desk.portable` globs from the
  frontmatter of `<home>/desk.md`, default — every .md outside `contexts/`.
  Prose only: code fences, inline code and link targets are skipped, and so is a
  line with the marker `lint:place-ok`.
- context-read-at-start: session files that did not fit the start slots were read
  before the first action.
"""
import re
from pathlib import Path

from . import ext, home as homelib
from .frontmatter import as_list
from .when import Call, glob_match

OK_MARK = "lint:place-ok"


# --------------------------------------------------------------- place words
def portable(path, home):
    try:
        rel = Path(path).resolve().relative_to(Path(home).resolve()).as_posix()
    except (ValueError, OSError):
        return False
    if not rel.endswith(".md"):
        return False
    globs = as_list(homelib.config(home).get("portable"))
    if globs:
        return any(glob_match(str(g), rel) for g in globs)
    return not rel.startswith("contexts/")


def all_words(home):
    return [w for c in homelib.load_contexts(home) for w in c.words()]


def place_hits(text, words):
    """[(line number, word)] in prose: outside fences, inline code and link targets."""
    hits, fence = [], False
    for n, line in enumerate((text or "").splitlines(), 1):
        if line.lstrip().startswith("```"):
            fence = not fence
            continue
        if fence or OK_MARK in line:
            continue
        prose = re.sub(r"`[^`]*`", "`", re.sub(r"\]\([^)]*\)", "]", line)).casefold()
        hits += [(n, w) for w in words if w.casefold() in prose]
    return hits


def _portable_hits(call, home):
    paths = [p for p in call.targets if portable(p, home)]
    if not paths:
        return [], []
    return paths, sorted({w for _, w in place_hits(call.new_text(), all_words(home))})


@ext.live("place-words", "в переносимом слое нет имён контекстов")
def place_words_live(call, session):
    paths, words = _portable_hits(call, session.home)
    if words:
        return ("ask", "desk: имена контекста в переносимом слое (" + ", ".join(words)
                + "): факт — в contexts/<контекст>/, правило — без имён, между ними ссылка. "
                  f"Осознанно — пометить строку маркером {OK_MARK}.")
    return None


@ext.compliance("place-words", "в переносимом слое нет имён контекстов")
def place_words_cases(record, home):
    cases = []
    for ev in record["events"]:
        if ev["kind"] != "tool_use":
            continue
        call = Call(tool=ev["name"], input=ev.get("input") or {}, cwd=record.get("cwd") or ".")
        paths, words = _portable_hits(call, home)
        if paths:
            cases.append((not words, f"{', '.join(paths)}: {', '.join(words)}"[:160]))
    return cases


@ext.lint("place-words", "имена контекстов в переносимом слое")
def place_words_lint(home):
    words = all_words(home)
    out = []
    if not words:
        return out
    for p in homelib.iter_markdown(home):
        if portable(p, home):
            for n, w in place_hits(p.read_text(encoding="utf-8", errors="replace"), words):
                out.append((p, n, f"имя контекста `{w}` в переносимом слое"))
    return out


# ---------------------------------------------------- context read at start
REST_MARK = "Не поместилось в слоты"
_PATH_LINE_RE = re.compile(r"^- (/\S+)$", re.M)
READ_TOOLS = ("Read", "Glob", "Grep", "TodoWrite", "ToolSearch")


@ext.compliance("context-read-at-start", "файлы сессии, не влезшие в слоты, прочитаны до первого действия")
def context_read_cases(record, home):
    listed = []
    for ev in record["events"]:
        if ev["kind"] == "attachment" and REST_MARK in ev["text"]:
            listed = _PATH_LINE_RE.findall(ev["text"].split(REST_MARK, 1)[1])
            break
    if not listed:
        return []
    read = set()
    for ev in record["events"]:
        if ev["kind"] != "tool_use":
            continue
        if ev["name"] == "Read":
            read.add((ev.get("input") or {}).get("file_path", ""))
        elif ev["name"] == "Bash":
            cmd = (ev.get("input") or {}).get("command", "")
            hit = [p for p in listed if p in cmd]
            if not ev.get("persisted"):
                read.update(hit)
            if not hit:
                break
        elif ev["name"] not in READ_TOOLS:
            break
    return [(p in read, p) for p in listed]
