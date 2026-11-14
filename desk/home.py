"""DESK_HOME, contexts and rule files.

DESK_HOME is the data root (rules, contexts, extensions); the engine ships no rules
of its own. Layout the engine relies on:

    <home>/**/*.md                         a rule file is any .md with `tier:` in frontmatter
    <home>/contexts/<name>/context.md      a context; `match:` says when it is active
    <home>/contexts/<name>/projects/<repo>/  rules of one repository of that context
    <home>/contexts/<name>/place-words.txt   names of that context (checks.place_words)
    <home>/ext/*.py                        extensions (desk.ext)

Everything else is the user's business.
"""
import fnmatch
import functools
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import frontmatter

TIERS = ("import", "session", "action", "link")
SKIP_DIRS = {"work", "node_modules", "__pycache__", "ext"}
QUIET_ENV = "DESK_HOOKS_OFF"


def desk_home():
    return Path(os.environ.get("DESK_HOME") or Path.home() / "desk").expanduser()


def quiet():
    """Hooks stay silent: service sessions (`claude -p` from a tool) set DESK_HOOKS_OFF."""
    return bool(os.environ.get(QUIET_ENV))


def is_inside(path, root):
    try:
        return Path(path or ".").expanduser().resolve().is_relative_to(Path(root).resolve())
    except (OSError, RuntimeError, ValueError):
        return False


# ------------------------------------------------------------------- git
@functools.lru_cache(maxsize=256)
def remotes_of(path):
    """`git remote -v` of the checkout containing path; no git — ""."""
    try:
        return subprocess.run(["git", "-C", str(path), "remote", "-v"],
                              capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return ""


def repo_name(remotes):
    """`git@example.com:acme/shop-api.git (fetch)` → shop-api: the name comes from the
    remote, not from the directory (worktrees and clones have arbitrary dir names)."""
    m = re.search(r"([^/:\s]+?)(?:\.git)?\s+\(fetch\)", remotes or "")
    return m.group(1) if m else ""


@functools.lru_cache(maxsize=1024)
def checkout_root(path):
    """Nearest parent with `.git` (file or dir); the path may not exist yet."""
    try:
        p = Path(path).expanduser().resolve()
    except (OSError, RuntimeError):
        return None
    for d in [p, *p.parents]:
        if (d / ".git").exists():
            return d
    return None


def repo_of_path(path):
    """(repo name, checkout root) of a file; ("", None) outside any checkout."""
    root = checkout_root(str(path))
    if root is None:
        return "", None
    return repo_name(remotes_of(str(root))), root


# ------------------------------------------------------------------ rules
@dataclass(eq=False)
class Rule:
    path: Path
    rel: str
    fm: dict
    context: str = None          # contexts/<context>/…
    repo: str = None             # contexts/<context>/projects/<repo>/…
    error: str = None            # frontmatter did not parse

    @property
    def tier(self):
        return self.fm.get("tier")

    @property
    def when(self):
        """Entries (mappings) and names of shared triggers (strings, desk.yml)."""
        return [w for w in frontmatter.as_list(self.fm.get("when")) if isinstance(w, (dict, str))]

    @property
    def guard(self):
        return [g for g in frontmatter.as_list(self.fm.get("guard")) if isinstance(g, dict)]

    @property
    def layer(self):
        """0 — repository, 1 — context, 2 — global: closer to the work goes first."""
        return 0 if self.repo else 1 if self.context else 2

    def text(self):
        try:
            return self.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return ""

    def body(self):
        return frontmatter.body(self.text())


def _scope(rel_parts):
    if len(rel_parts) >= 3 and rel_parts[0] == "contexts":
        ctx = rel_parts[1]
        if len(rel_parts) >= 5 and rel_parts[2] == "projects":
            return ctx, rel_parts[3]
        return ctx, None
    return None, None


def iter_markdown(home):
    home = Path(home)
    for dirpath, dirnames, filenames in os.walk(home):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS)
        for f in sorted(filenames):
            if f.endswith(".md"):
                yield Path(dirpath) / f


def _head(path, limit=16384):
    """Only the frontmatter is needed to know a file's tier: read the head."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read(limit)


def load_rules(home=None, include_untiered=False):
    """Rule files of DESK_HOME. A file is a rule when its frontmatter has `tier`;
    a file whose frontmatter does not parse is returned with `error` set so the
    linter can say so (hooks skip it)."""
    home = Path(home or desk_home())
    out = []
    for p in iter_markdown(home):
        try:
            head = _head(p)
        except OSError:
            continue
        if not head.startswith("---"):
            if include_untiered:
                out.append(_rule(home, p, {}, None))
            continue
        error = None
        try:
            fm = frontmatter.parse(head)
        except frontmatter.FrontmatterError as e:
            fm, error = {}, str(e)
        if error and not re.search(r"^tier\s*:", head, re.M):
            continue    # not a rule (a skill manifest, say): its YAML is not ours to judge
        if error or "tier" in fm or include_untiered:
            out.append(_rule(home, p, fm, error))
    return out


def _rule(home, p, fm, error):
    rel = p.relative_to(home)
    ctx, repo = _scope(rel.parts)
    return Rule(path=p, rel=rel.as_posix(), fm=fm, context=ctx, repo=repo, error=error)


# --------------------------------------------------------------- contexts
@dataclass
class Context:
    name: str
    path: Path
    fm: dict = field(default_factory=dict)

    @property
    def match(self):
        m = self.fm.get("match")
        return m if isinstance(m, dict) else {}

    @property
    def hub(self):
        return self.path / "context.md"

    def repos(self):
        d = self.path / "projects"
        return sorted(p.name for p in d.iterdir() if p.is_dir()) if d.is_dir() else []

    def words(self):
        """Names of this context that must not leak into other layers."""
        f = self.path / "place-words.txt"
        try:
            raw = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        return [w for w in (line.split("#", 1)[0].strip() for line in raw) if w]


def load_contexts(home=None):
    root = Path(home or desk_home()) / "contexts"
    out = []
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        hub = d / "context.md"
        if d.is_dir() and hub.exists():
            fm, _ = frontmatter.read(hub)
            out.append(Context(d.name, d, fm))
    return out


def _path_globs(m):
    return [str(Path(g).expanduser()) for g in frontmatter.as_list(m.get("path"))]


def pick_context(contexts, session_dir, remotes):
    """(context or None, basis). Order: session inside contexts/<name>/ of DESK_HOME →
    `match.remote` (substring of `git remote -v`) or `match.path` (glob of the session
    dir) → the context with `match.default: true` → none (global rules only)."""
    here = Path(session_dir or ".").expanduser()
    for c in contexts:
        if is_inside(here, c.path):
            return c, f"папка сессии contexts/{c.name}/"
    for c in contexts:
        m = c.match
        if remotes and any(r and str(r) in remotes for r in frontmatter.as_list(m.get("remote"))):
            return c, "git remote -v"
        if any(fnmatch.fnmatchcase(str(here), g) or fnmatch.fnmatchcase(str(here), g.rstrip("/") + "/*")
               for g in _path_globs(m)):
            return c, "путь сессии"
    for c in contexts:
        if c.match.get("default") is True:
            return c, "контекст по умолчанию"
    return None, "ни один контекст не подошёл"


# ---------------------------------------------------------------- session
def path_slug(path):
    """Harness project folder name in ~/.claude/projects: non-alphanumerics → `-`."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def session_dir(data, project_dir=None):
    """The session's directory, not the shell's momentary cwd from the hook input.

    `cd` in Bash moves the input cwd; changing the session directory moves the
    transcript folder; CLAUDE_PROJECT_DIR keeps the start dir. The folder of
    `transcript_path` names the current session dir — pick the candidate
    (CLAUDE_PROJECT_DIR, cwd and its parents) whose slug equals it; else cwd."""
    data = data or {}
    cwd = data.get("cwd") or "."
    slug = Path(data.get("transcript_path") or "").parent.name
    if not slug:
        return cwd
    proj = os.environ.get("CLAUDE_PROJECT_DIR") if project_dir is None else project_dir
    here = Path(cwd)
    for cand in ([Path(proj)] if proj else []) + [here, *here.parents]:
        if path_slug(cand) == slug:
            return str(cand)
    return cwd


@dataclass
class Session:
    home: Path
    cwd: str
    session_dir: str
    remotes: str
    context: Context
    basis: str
    repo: str
    session_id: str = ""
    contexts: list = field(default_factory=list)

    @property
    def in_home(self):
        return is_inside(self.session_dir, self.home)

    @property
    def context_name(self):
        return self.context.name if self.context else None

    def known_repos(self):
        """Repo names the call text is searched for: every context's projects/."""
        return sorted({r for c in self.contexts for r in c.repos()})


def session_from(data, home=None, project_dir=None):
    data = data or {}
    home = Path(home or desk_home())
    sdir = session_dir(data, project_dir)
    remotes = remotes_of(str(sdir))
    contexts = load_contexts(home)
    ctx, basis = pick_context(contexts, sdir, remotes)
    return Session(home=home, cwd=data.get("cwd") or ".", session_dir=str(sdir),
                   remotes=remotes, context=ctx, basis=basis, repo=repo_name(remotes),
                   session_id=data.get("session_id") or "", contexts=contexts)


def in_scope(rule, session, repos=None):
    """Rule belongs to this session: global, or of the active context. A repository
    rule goes with its repository, not with the session: it applies whenever that
    repo is among `repos` (default: the session's repository), whichever context is
    active — paid code stays guarded when another session touches it. Only when two
    contexts know a repo by the same name does the active context decide."""
    if rule.context is None:
        return True
    active = session.context.name if session.context else None
    if rule.repo is None:
        return rule.context == active
    if rule.repo not in (repos if repos is not None else {session.repo}):
        return False
    if rule.context == active:
        return True
    owners = [c.name for c in session.contexts if rule.repo in c.repos()]
    return owners == [rule.context]


def skipped(rule, session):
    """`skip_in:` — context names, plus `desk` for a session inside DESK_HOME
    (there rules are edited, not applied)."""
    skip = set(frontmatter.as_list(rule.fm.get("skip_in")))
    if "desk" in skip and session.in_home:
        return True
    return session.context_name in skip


CONFIG_FILE = "desk.yml"
_config_cache = {}


def config(home=None):
    """Engine settings: `<home>/desk.yml` — `triggers:` (named triggers), `portable:`,
    `entry:`. A file of its own, not a rule's frontmatter: the entry rule is imported
    into every session and must not carry engine data. Missing or broken — {}
    (lint reports it)."""
    return _load_config(Path(home or desk_home()) / CONFIG_FILE)


def context_config(home, context):
    """`contexts/<context>/desk.yml` — what a place adds (its named triggers)."""
    if not context:
        return {}
    return _load_config(Path(home or desk_home()) / "contexts" / str(context) / CONFIG_FILE)


def _load_config(f):
    try:
        key = (str(f), f.stat().st_mtime)
    except OSError:
        return {}
    if key not in _config_cache:
        try:
            raw = frontmatter.loads(f.read_text(encoding="utf-8")) or {}
        except (frontmatter.FrontmatterError, OSError, UnicodeDecodeError):
            raw = {}
        _config_cache[key] = raw if isinstance(raw, dict) else {}
    return _config_cache[key]
