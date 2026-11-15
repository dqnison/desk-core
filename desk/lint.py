"""Structural checks of DESK_HOME, no model.

  frontmatter — parses; `tier` is one of import/session/action/link
  when        — known keys, regexes compile, `prompt` stands alone, `gate` needs a
                matcher; action without `when:` is never delivered; link with
                `when:` contradicts itself
  guard       — exactly one decision, known scope, regex compiles, `why` present
  budget      — an action rule fits one portion (deliver.BUDGET); an import rule
                is at most IMPORT_MAX bytes (it costs every session)
  contexts    — at most one `match.default`; `match` has known keys
  links       — relative markdown links lead to existing files and headings
  imports     — with `desk.entry` (or DESK_ENTRY) set: every import rule is
                `@`-imported by the entry file, and only those
  extensions  — every `desk.ext.lint` check (place-words is one)

Output: `path:line: [check] message`; exit 1 when anything is found.
"""
import os
import re
from pathlib import Path

from . import deliver as dl, ext, frontmatter, guards, home as homelib, when as wh

IMPORT_MAX = 8 * 1024
MATCH_KEYS = ("remote", "path", "default")


class Problems(list):
    def add(self, home, path, line, check, message):
        try:
            rel = Path(path).relative_to(home).as_posix()
        except ValueError:
            rel = str(path)
        self.append((rel, line or 1, check, message))


def _fm_line(text, key):
    for n, line in enumerate(text.splitlines(), 1):
        if re.match(rf"^{re.escape(key)}\s*:", line):
            return n
    return 1


def check_rule(rule, home, out, brought=frozenset()):
    if rule.error:
        out.add(home, rule.path, None, "frontmatter", rule.error)
        return
    text = rule.text()
    if rule.tier not in homelib.TIERS:
        out.add(home, rule.path, _fm_line(text, "tier"), "frontmatter",
                f"tier `{rule.tier}` — one of {', '.join(homelib.TIERS)}")
    raw_when = frontmatter.as_list(rule.fm.get("when"))
    named = wh.named_triggers(home, rule.context)
    if rule.context is None:     # a portable rule may name what only places define
        for c in homelib.load_contexts(home):
            named = {**wh.named_triggers(home, c.name), **named}
    for i, entry in enumerate(raw_when, 1):
        if isinstance(entry, str):
            if entry not in named:
                out.add(home, rule.path, _fm_line(text, "when"), "when",
                        f"entry {i}: no trigger `{entry}` in {wh.NAMED_FILE}")
            continue
        for p in lint_when(entry):
            out.add(home, rule.path, _fm_line(text, "when"), "when", f"entry {i}: {p}")
    if rule.tier == "action" and not raw_when and rule.rel not in brought:
        out.add(home, rule.path, _fm_line(text, "tier"), "when",
                "action rule without `when:` (and no rule's `also:` names it) is never delivered")
    for rel in frontmatter.as_list(rule.fm.get("also")):
        if not (Path(home) / str(rel)).is_file():
            out.add(home, rule.path, _fm_line(text, "also"), "when", f"`also:` names a missing file {rel}")
    if rule.tier in ("link", "import") and raw_when:
        out.add(home, rule.path, _fm_line(text, "when"), "when",
                f"`when:` on a {rule.tier} rule: {rule.tier} has no hook carrier")
    for i, g in enumerate(frontmatter.as_list(rule.fm.get("guard")), 1):
        for p in guards.lint_guard(g):
            out.add(home, rule.path, _fm_line(text, "guard"), "guard", f"entry {i}: {p}")
    size = len(rule.body().encode("utf-8"))
    if rule.tier == "action" and size > dl.BUDGET:
        out.add(home, rule.path, None, "budget",
                f"action body {dl.kb(size)} > portion {dl.kb(dl.BUDGET)}: it arrives in parts")
    if rule.tier == "import" and len(text.encode("utf-8")) > IMPORT_MAX:
        out.add(home, rule.path, None, "budget", f"import rule > {dl.kb(IMPORT_MAX)}: it costs every session")


def lint_when(entry):
    if not isinstance(entry, dict):
        return ["entry is not a mapping"]
    probs = []
    unknown = [k for k in entry if k not in wh.KEYS]
    if unknown:
        probs.append(f"unknown keys: {', '.join(unknown)} (known: {', '.join(wh.KEYS)})")
    matchers = [k for k in entry if k in wh.MATCHERS]
    if not matchers:
        probs.append("no matcher (gate alone never fires)")
    if "prompt" in entry and len(matchers) > 1:
        probs.append("`prompt` cannot be combined with tool matchers")
    for k in ("command", "prompt"):
        if k in entry:
            try:
                re.compile(str(entry[k]))
            except re.error as e:
                probs.append(f"bad regex in {k}: {e}")
    if "input" in entry:
        if not isinstance(entry["input"], dict) or not entry["input"]:
            probs.append("`input` must be a mapping field → regex")
        else:
            for f, rx in entry["input"].items():
                try:
                    re.compile(str(rx))
                except re.error as e:
                    probs.append(f"bad regex in input.{f}: {e}")
    if "git" in entry and entry["git"] != "write" and not isinstance(entry["git"], list):
        probs.append("`git` is `write` or a list of subcommands")
    if "desk" in entry and entry["desk"] is not True:
        probs.append("`desk` takes only `true`")
    if "gate" in entry and entry["gate"] is not True:
        probs.append("`gate` takes only `true`")
    return probs


def check_named(home, out):
    _check_config(home, Path(home) / homelib.CONFIG_FILE, out, root=True)
    for c in homelib.load_contexts(home):
        _check_config(home, c.path / homelib.CONFIG_FILE, out, root=False)


def _check_config(home, f, out, root):
    if not f.exists():
        return
    try:
        raw = frontmatter.loads(f.read_text(encoding="utf-8")) or {}
    except frontmatter.FrontmatterError as e:
        out.add(home, f, e.line, "config", str(e))
        return
    if not isinstance(raw, dict):
        out.add(home, f, None, "config", "desk.yml is not a mapping")
        return
    allowed = ("triggers", "portable", "entry", "header") if root else ("triggers",)
    unknown = [k for k in raw if k not in allowed]
    if unknown:
        out.add(home, f, None, "config", f"unknown keys: {', '.join(unknown)}")
    triggers = raw.get("triggers") or {}
    if not isinstance(triggers, dict):
        out.add(home, f, None, "config", "`triggers` is a mapping name → entries")
        return
    for name, entries in triggers.items():
        for i, entry in enumerate(frontmatter.as_list(entries), 1):
            for p in lint_when(entry):
                out.add(home, f, None, "when", f"{name} entry {i}: {p}")


def check_contexts(home, out):
    defaults = []
    for c in homelib.load_contexts(home):
        m = c.fm.get("match")
        if m is None:
            continue
        if not isinstance(m, dict):
            out.add(home, c.hub, None, "contexts", "`match` must be a mapping")
            continue
        unknown = [k for k in m if k not in MATCH_KEYS]
        if unknown:
            out.add(home, c.hub, None, "contexts", f"unknown match keys: {', '.join(unknown)}")
        if m.get("default") is True:
            defaults.append(c.name)
    if len(defaults) > 1:
        out.add(home, Path(home) / "contexts", None, "contexts",
                f"more than one default context: {', '.join(defaults)}")


_LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def slug(heading, collapse=True):
    """Anchor: lowercase, punctuation dropped, spaces → `-`. Obsidian collapses a run
    of spaces into one dash, GitHub does not (`a — b` → `a-b` / `a--b`); both count."""
    s = re.sub(r"[^\w\s-]", "", heading.strip()).strip().casefold()
    return re.sub(r"\s+" if collapse else r"\s", "-", s)


def anchors(path):
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return set()
    out, fence = set(), False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fence = not fence
        elif not fence and re.match(r"#{1,6}\s", line):
            out |= {slug(line.lstrip("#")), slug(line.lstrip("#"), collapse=False)}
    return out


def check_links(home, out):
    for p in homelib.iter_markdown(home):
        fence = False
        for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if line.lstrip().startswith("```"):
                fence = not fence
                continue
            if fence:
                continue
            for target in _LINK_RE.findall(re.sub(r"`[^`]*`", "", line)):
                if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I):
                    continue      # http:, mailto:, …
                file, _, anchor = target.partition("#")
                dest = p if not file else (p.parent / file)
                if file and not dest.exists():
                    out.add(home, p, n, "links", f"broken link {target}")
                elif anchor and dest.suffix == ".md" and slug(anchor) not in anchors(dest) \
                        and anchor not in anchors(dest):
                    out.add(home, p, n, "links", f"no heading for #{anchor} in {file or p.name}")


def entry_file(home):
    env = os.environ.get("DESK_ENTRY")
    cfg = homelib.config(home).get("entry")
    raw = env or cfg
    return Path(str(raw)).expanduser() if raw else None


def imported(entry):
    try:
        text = entry.read_text(encoding="utf-8")
    except OSError:
        return None
    return {str(Path(m).expanduser().resolve()) for m in re.findall(r"^@(\S+)", text, re.M)}


def check_imports(home, rules, out):
    entry = entry_file(home)
    if entry is None:
        return
    got = imported(entry)
    if got is None:
        out.add(home, entry, None, "imports", "entry file is not readable")
        return
    for r in rules:
        is_imp = str(r.path.resolve()) in got
        if r.tier == "import" and not is_imp:
            out.add(home, r.path, None, "imports", f"import rule is not @-imported by {entry}")
        elif is_imp and r.tier != "import":
            out.add(home, r.path, None, "imports", f"@-imported by {entry} but tier is {r.tier}")


def run(home=None):
    home = Path(home or homelib.desk_home())
    out = Problems()
    rules = homelib.load_rules(home)
    brought = {str(x) for r in rules for x in frontmatter.as_list(r.fm.get("also"))}
    for r in rules:
        check_rule(r, home, out, brought)
    check_named(home, out)
    check_contexts(home, out)
    check_links(home, out)
    check_imports(home, [r for r in rules if not r.error], out)
    for name, err in ext.load(home):
        out.add(home, Path(home) / "ext" / name, None, "extensions", err)
    for check in ext.LINT.values():
        try:
            for path, line, message in check.fn(home):
                out.add(home, path, line, check.id, message)
        except Exception as e:
            out.add(home, home, None, check.id, f"check crashed: {e!r}")
    return sorted(out)


def format_problems(problems):
    return "\n".join(f"{rel}:{line}: [{check}] {message}" for rel, line, check, message in problems)
