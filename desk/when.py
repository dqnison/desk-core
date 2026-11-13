"""Triggers as data: the `when:` list in a rule's frontmatter.

A rule fires when any of its `when:` entries matches; inside an entry every key
must match. Keys (primitives):

    tool:    glob or list of globs over the tool name          (`Bash`, `mcp__metrics*`)
    command: regex over the Bash command (implies tool Bash)
    input:   {field: regex} over tool input fields (`a.b` reaches nested fields)
    paths:   glob or list over the files the call writes — Edit/Write targets and
             what a Bash command writes (redirects, tee, sed -i, cp/mv, rm, scripts)
    skill:   skill name or list (`plugin:name` and `name` are the same)
    git:     `write` — a git subcommand outside the read-only list; or a list of
             subcommands (`[worktree]`)
    desk:    true — the call writes into DESK_HOME, or a Bash call runs there
    prompt:  regex over the user's prompt (UserPromptSubmit only; not combinable)
    gate:    true — option, not a matcher: the first matching call while the rule is
             not yet in context is stopped (deny) with the rule text; the retry passes

Globs: `**` crosses directories, `*` and `?` do not; a trailing `/` means "anything
under". An absolute (or `~`) glob matches the absolute path; a relative one matches
the path relative to the file's checkout root or to DESK_HOME. For a repository
rule (`contexts/<c>/projects/<repo>/`) a relative glob applies to that repository's
files only.
"""
import fnmatch
import functools
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import home as homelib
from .frontmatter import as_list

MATCHERS = ("tool", "command", "input", "paths", "skill", "git", "desk", "prompt")
OPTIONS = ("gate",)
KEYS = MATCHERS + OPTIONS
EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")


# ---------------------------------------------------------------------- call
@dataclass
class Call:
    """One thing a hook reacts to: a tool call or a user prompt."""
    tool: str = ""
    input: dict = field(default_factory=dict)
    prompt: str = None
    cwd: str = "."

    @classmethod
    def from_hook(cls, data):
        if data.get("hook_event_name") == "UserPromptSubmit" or "prompt" in data and "tool_name" not in data:
            return cls(prompt=data.get("prompt") or "", cwd=data.get("cwd") or ".")
        inp = data.get("tool_input")
        return cls(tool=data.get("tool_name") or "", input=inp if isinstance(inp, dict) else {},
                   cwd=data.get("cwd") or ".")

    @property
    def is_prompt(self):
        return self.prompt is not None

    @property
    def command(self):
        return str(self.input.get("command") or "") if self.tool == "Bash" else ""

    @functools.cached_property
    def targets(self):
        """Absolute paths the call writes."""
        if self.tool in EDIT_TOOLS:
            fp = self.input.get("file_path") or self.input.get("notebook_path") or ""
            if not fp:
                return []
            p = Path(fp).expanduser()
            return [os.path.normpath(str(p if p.is_absolute() else Path(self.cwd) / p))]
        if self.tool == "Bash":
            return write_targets(self.command, self.cwd)
        return []

    @functools.cached_property
    def dirs(self):
        """Directories a Bash command works in: cwd, `cd X`, `git -C X`."""
        if self.tool != "Bash":
            return []
        cmd = expand_vars(self.command)
        base = effective_cwd(cmd, self.cwd)
        out = [os.path.normpath(str(base))]
        out += _resolve([m for m in _GIT_C_RE.findall(cmd)], base)
        return out

    def new_text(self):
        """Text the call puts into files: Edit new_string, Write content, MultiEdit
        edits; Bash — the command itself when it writes (a heredoc lives in it)."""
        inp = self.input
        if self.tool == "MultiEdit":
            return "\n".join(str((e or {}).get("new_string", "")) for e in inp.get("edits") or [])
        if self.tool == "Bash":
            return self.command if self.targets else ""
        if self.tool in EDIT_TOOLS:
            return str(inp.get("new_string") or inp.get("content") or inp.get("new_source") or "")
        return ""


# ------------------------------------------------------------- shell parsing
_ASSIGN_RE = re.compile(r"""(?:^|[;&|]\s*|\n\s*)([A-Za-z_]\w*)=(["']?)([^\s;&|"']+)\2""")
_CD_RE = re.compile(r"""(?:^|\n|[;&]\s*)\s*cd\s+(["']?)([^\s;&|"']+)\1\s*(?:&&|;|\n|$)""")
_GIT_C_RE = re.compile(r"""\bgit\s+-C\s+["']?([^\s"';&|]+)""")
_REDIRECT_RE = re.compile(r"""(?<![<>0-9&])>>?(?!&)\s*["']?([^\s"'|&;<>()]+)|\btee\b(?:\s+-a)?\s+["']?([^\s"'|&;<>]+)""")
_INPLACE_RE = re.compile(r"\bsed\s+-[a-zA-Z]*i|\bperl\s+-[0-9a-zA-Z]*i")
_CPMV_RE = re.compile(r"""(?:^|[;&|]\s*|\n\s*|\bsudo\s+)(?:cp|mv|ln)\s+(?:-\S+\s+)*(?:["']?[^\s"';&|]+["']?\s+)+["']?([^\s"';&|]+)""")
_RM_RE = re.compile(r"""(?:^|[;&|]\s*|\n\s*)(?:rm|rmdir|touch|mkdir)\s+((?:["']?[^\s"';&|]+["']?\s*)+)""")
_GIT_RESTORE_RE = re.compile(r"\bgit\s+(?:checkout|restore)\b[^\n;&|]*?\s--\s+([^\n;&|]+)")
_INTERP_RE = re.compile(r"\b(python3?|perl|ruby|node|php)\b")
_SCRIPT_WRITE_RE = re.compile(
    r"""open\([^)]*["'][wa]\+?b?["']|\.write_text\(|\.write_bytes\(|\.write\(|writelines\(|writeFile|file_put_contents""")
_PATHISH_RE = re.compile(
    r"""(?<![\w@.-])((?:~/|\.{1,2}/|/)?[\w.@-]+(?:/[\w.@-]+)+|\.?[\w@-]+\.[a-zA-Z]{1,6}|\.[\w-]{2,})(?![\w/])""")
_NOT_PATH = ("/dev/", "http://", "https://")


def expand_vars(cmd):
    """Substitute `NAME=value` assignments of the command itself into `$NAME`/`${NAME}`."""
    for name, _, val in _ASSIGN_RE.findall(cmd or ""):
        cmd = re.sub(r"\$\{?" + re.escape(name) + r"\}?(?![\w])", lambda _m, v=val: v, cmd)
    return cmd


def effective_cwd(cmd, cwd):
    """Base of relative paths: the first `cd X` of the command, else cwd."""
    base = Path(cwd or ".").expanduser()
    m = _CD_RE.search(cmd or "")
    if m:
        target = Path(m.group(2)).expanduser()
        base = target if target.is_absolute() else base / target
    return base


def _resolve(tokens, base):
    out = []
    for t in tokens:
        t = t.strip().strip("'\"")
        if not t or t.isdigit() or t.startswith("-") or any(t.startswith(x) or x in t for x in _NOT_PATH):
            continue
        q = Path(t).expanduser()
        # normpath, not resolve: no symlink games, `..` collapses to one spelling
        out.append(os.path.normpath(str(q if q.is_absolute() else Path(base) / q)))
    return out


def write_targets(cmd, cwd="."):
    """Paths a shell command writes: `> f`, `>> f`, `tee f`, `cp/mv/ln … f`,
    `rm/touch/mkdir f…`, `git checkout|restore … -- f`, `sed -i`/`perl -i` … f, and
    every path in an interpreter script that writes (`open(p, "w")`, `write_text`):
    the text does not say which one, so all count."""
    cmd = expand_vars(cmd or "")
    base = effective_cwd(cmd, cwd)
    raw = [a or b for a, b in _REDIRECT_RE.findall(cmd)]
    raw += _CPMV_RE.findall(cmd)
    for group in _RM_RE.findall(cmd):
        raw += group.split()
    for group in _GIT_RESTORE_RE.findall(cmd):
        raw += group.split()
    scripted = _INTERP_RE.search(cmd) and _SCRIPT_WRITE_RE.search(cmd)
    if _INPLACE_RE.search(cmd) or scripted:
        raw += _PATHISH_RE.findall(cmd)
    out = []
    for p in _resolve(raw, base):
        if p not in out:
            out.append(p)
    return out


_READ_CMD_RE = re.compile(r"\b(cat|sed|head|tail|less|more|bat)\b")


def read_targets(cmd, cwd="."):
    """Existing files a read-only shell command prints (cat, sed -n, head, tail)."""
    cmd = expand_vars(cmd or "")
    if not _READ_CMD_RE.search(cmd) or _INPLACE_RE.search(cmd):
        return []
    base = effective_cwd(cmd, cwd)
    return [p for p in _resolve(_PATHISH_RE.findall(cmd), base) if Path(p).is_file()]


# Subcommands that change nothing: rules about branches and commits are not for them.
GIT_READ_ONLY = {"status", "log", "show", "ls-files", "ls-tree", "ls-remote", "blame",
                 "grep", "rev-parse", "rev-list", "describe", "cat-file", "shortlog",
                 "reflog", "remote", "config", "for-each-ref", "name-rev", "help",
                 "version", "check-ignore", "merge-base", "count-objects", "var"}
_GIT_RE = re.compile(r"(?:^|[;&|(\s])git((?:\s+(?:-C\s+\S+|-c\s+\S+|--?[\w-]+(?:=\S+)?))*)\s+([a-z][\w-]*)")


def git_subcommands(cmd):
    """Subcommands of every git call, global options skipped (`git -C x --no-pager log` → log)."""
    return [m.group(2) for m in _GIT_RE.finditer(cmd or "")]


# -------------------------------------------------------------------- globs
@functools.lru_cache(maxsize=512)
def _glob_re(pattern):
    if pattern.endswith("/"):
        pattern += "**"
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def glob_match(pattern, path):
    return _glob_re(pattern).match(path) is not None


def path_candidates(path, home):
    """Spellings of an absolute path a relative glob is matched against."""
    out = []
    root = homelib.checkout_root(path)
    for base in ([root] if root else []) + [Path(home)]:
        try:
            rel = Path(path).relative_to(base).as_posix()
        except ValueError:
            try:
                rel = Path(path).resolve().relative_to(Path(base).resolve()).as_posix()
            except (ValueError, OSError):
                continue
        if rel not in out:
            out.append(rel)
    return out


def paths_match(globs, path, home, repo=None):
    """Any glob matches the path. `repo` — the rule is a repository rule: relative
    globs apply only to files of that repository, relative to its root."""
    globs = [str(g) for g in as_list(globs)]
    absolute = [g for g in globs if g.startswith(("/", "~"))]
    relative = [g for g in globs if not g.startswith(("/", "~"))]
    if any(glob_match(str(Path(g).expanduser()), path) for g in absolute):
        return True
    if not relative:
        return False
    if repo:
        name, root = homelib.repo_of_path(path)
        if name != repo or root is None:
            return False
        try:
            cands = [Path(path).resolve().relative_to(root).as_posix()]
        except (ValueError, OSError):
            return False
    else:
        cands = path_candidates(path, home)
    return any(glob_match(g, c) for g in relative for c in cands)


# ------------------------------------------------------------------ matching
def _regex(rx, flags=0):
    """`prompt` is matched case-insensitively (people type as they like); `command`
    and `input` case-sensitively (commands and paths are case-sensitive) — add
    `(?i)` to a pattern to change that."""
    try:
        return re.compile(str(rx), flags)
    except re.error:
        return None


def _input_field(inp, dotted):
    cur = inp
    for part in str(dotted).split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    if isinstance(cur, (dict, list)):
        return json.dumps(cur, ensure_ascii=False)
    return str(cur)


def match_entry(entry, call, home, repo=None):
    """Description of why the entry matches the call (for `desk why` and the hook
    header), or None. Unknown keys never match: a typo must not fire everywhere."""
    if not isinstance(entry, dict) or not any(k in entry for k in MATCHERS):
        return None
    if any(k not in KEYS for k in entry):
        return None
    if "prompt" in entry:
        if not call.is_prompt or len([k for k in entry if k in MATCHERS]) > 1:
            return None
        rx = _regex(entry["prompt"], re.I)
        m = rx.search(call.prompt or "") if rx else None
        return f"реплика «{m.group(0)}»" if m else None
    if call.is_prompt:
        return None
    why = []
    if "tool" in entry:
        globs = [str(g) for g in as_list(entry["tool"])]
        if not any(fnmatch.fnmatchcase(call.tool, g) for g in globs):
            return None
        why.append(f"tool {call.tool}")
    if "command" in entry:
        rx = _regex(entry["command"])
        m = rx.search(call.command) if rx and call.tool == "Bash" else None
        if not m:
            return None
        why.append(f"команда «{m.group(0)[:40]}»")
    if "input" in entry:
        spec = entry["input"]
        if not isinstance(spec, dict) or not spec:
            return None
        for fld, rx in spec.items():
            val = _input_field(call.input, fld)
            r = _regex(rx)
            if val is None or not r or not r.search(val):
                return None
        why.append("поля " + ", ".join(spec))
    if "skill" in entry:
        name = str(call.input.get("skill") or "").split(":")[-1] if call.tool == "Skill" else ""
        if not name or name not in [str(s).split(":")[-1] for s in as_list(entry["skill"])]:
            return None
        why.append(f"скилл {name}")
    if "git" in entry:
        subs = git_subcommands(call.command) if call.tool == "Bash" else []
        want = entry["git"]
        if want == "write":
            hit = [s for s in subs if s not in GIT_READ_ONLY]
        else:
            hit = [s for s in subs if s in [str(w) for w in as_list(want)]]
        if not hit:
            return None
        why.append(f"git {hit[0]}")
    if "paths" in entry:
        hit = [p for p in call.targets if paths_match(entry["paths"], p, home, repo)]
        if not hit:
            return None
        why.append(f"запись {_short(hit[0], home)}")
    if "desk" in entry:
        if entry["desk"] is not True or not touches_home(call, home):
            return None
        why.append("desk")
    return ", ".join(why)


def touches_home(call, home):
    """The call writes into DESK_HOME, or a Bash command runs from a shell already
    in it (any command — the session works on the desk itself), or a git command
    that changes something is pointed there (`cd`, `git -C`). Reading desk files
    from another session — `cd ~/desk && grep …` included — is not touching it."""
    if any(homelib.is_inside(p, home) for p in call.targets):
        return True
    if call.tool != "Bash":
        return False
    if homelib.is_inside(call.cwd, home):
        return True
    writes_git = any(s not in GIT_READ_ONLY | {"diff"} for s in git_subcommands(call.command))
    return writes_git and any(homelib.is_inside(d, home) for d in call.dirs)


def _short(path, home):
    for base in (home, Path.home()):
        try:
            return Path(path).relative_to(base).as_posix()
        except ValueError:
            pass
    return path


@dataclass
class Fired:
    rule: object
    entries: list          # [(entry dict or None, description)]

    @property
    def gated(self):
        return any(isinstance(e, dict) and e.get("gate") is True for e, _ in self.entries)

    @property
    def why(self):
        return "; ".join(d for _, d in self.entries)


# ------------------------------------------------------------ named triggers
NAMED_FILE = "desk.yml"


def named_triggers(home):
    """`triggers:` of `<home>/desk.yml`: name → list of `when:` entries. A rule may
    write a name instead of an entry (`when: [mr]`) — one definition for a trigger
    many rules share."""
    raw = homelib.config(home).get("triggers")
    if not isinstance(raw, dict):
        return {}
    return {str(k): [e for e in as_list(v) if isinstance(e, dict)] for k, v in raw.items()}


def entries_of(rule, named):
    """[(entry, label)] of a rule's `when:` with names expanded; label — the name or None."""
    out = []
    for item in rule.when:
        if isinstance(item, dict):
            out.append((item, None))
        elif isinstance(item, str):
            out += [(e, item) for e in named.get(item, [])]
    return out


def fired(rules, call, home):
    """Rules whose `when:` matches the call, plus the rules they bring along
    (`also:` — paths relative to DESK_HOME): [Fired]."""
    named = named_triggers(home)
    out = []
    for r in rules:
        hits = []
        for entry, label in entries_of(r, named):
            d = match_entry(entry, call, home, r.repo)
            if d:
                hits.append((entry, f"{label}: {d}" if label else d))
        if hits:
            out.append(Fired(r, hits))
    by_rel = {r.rel: r for r in rules}
    have = {f.rule.rel for f in out}
    for f in list(out):
        for rel in as_list(f.rule.fm.get("also")):
            r = by_rel.get(str(rel))
            if r is not None and r.rel not in have:
                have.add(r.rel)
                out.append(Fired(r, [(None, f"вместе с {f.rule.rel}")]))
    return out


def repos_touched(call, session):
    """Repositories a call works with: the session's; the repos (by remote) of files
    it writes and of existing paths in a Bash command — `cd`, `git -C`, variables,
    so a worktree in a directory of any name counts as its repo; and known repo
    names standing as a path segment (a checkout that is gone). Paths inside
    DESK_HOME are desk files, not repository work."""
    repos = {session.repo} if session.repo else set()
    paths = list(call.targets)
    if call.tool == "Bash":
        cmd = expand_vars(call.command)
        base = effective_cwd(cmd, call.cwd)
        paths += call.dirs
        paths += [p for p in _resolve(_PATHISH_RE.findall(cmd), base)
                  if p.startswith("/") and os.path.exists(p)]
    for p in paths:
        if homelib.is_inside(p, session.home):
            continue
        name, _ = homelib.repo_of_path(p)
        if name:
            repos.add(name)
    if call.tool == "Bash":
        text = call.command.replace(str(session.home), "")
        repos |= {r for r in session.known_repos() if re.search(r"/" + re.escape(r) + r"(?![\w.-])", text)}
    return repos
