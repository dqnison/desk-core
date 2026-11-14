"""Guards: `deny`/`ask` before the call — the only thing that acts on the call itself.

Data form, in a rule's frontmatter (active whenever the rule is in scope, regardless
of its `when:`):

    guard:
      - deny: 'Co-Authored-By|Generated with'    # regex; a match is a violation
        in: commit-message                       # where to look (default: command)
        why: no tool attribution in commits
      - deny-unless: '^(feat|fix|chore)\\((no-ref|[A-Z]+-\\d+)\\): '
        in: commit-subject                       # every subject must match
      - ask: 'billing/**'                        # for `in: paths` — globs, not a regex
        in: paths
        why: paid API — read the rule first

Decisions: `deny`, `ask`, `deny-unless`, `ask-unless`. Scopes: `command` (Bash
command), `commit-message` (each `git commit` segment with its heredoc),
`commit-subject` (first line of each `-m` message that can be read), `content`
(text the call writes), `paths` (files the call writes). Optional keys: `paths`
(globs: the guard applies only to calls writing such files), `id`, `since`
(date the compliance measure starts from).

`ask` is asked once per session per guard; `deny` every time. DESK_NO_DENY=1 turns
denies off (gates included), asks stay. Extensions add live checks (desk.ext).

Compliance of a data guard is derived from the same description: for deny-kinds,
each call in scope is a case, ok when not violating; for ask-kinds, each matching
call is a case, ok when the rule was in context before it (delivered or read).
"""
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import ext, home as homelib, messages as msg
from .when import paths_match

SCOPES = ("command", "commit-message", "commit-subject", "content", "paths")
DECISIONS = ("deny", "ask", "deny-unless", "ask-unless")
KEYS = DECISIONS + ("in", "why", "paths", "id", "since")
NO_DENY_ENV = "DESK_NO_DENY"


@dataclass
class Decision:
    kind: str           # deny | ask
    id: str
    reason: str
    rule: object = None


# --------------------------------------------------------------- commits
COMMIT_RE = re.compile(r"^(?:\S*/)?git(?:\s+(?:-[Cc]\s+\S+|--?[\w-]+(?:=\S+)?))*\s+commit\b")
_HEREDOC_MSG_RE = re.compile(r"""\$\(cat\s+<<-?\s*['"]?(\w+)['"]?\s*\n(.*?)\n""", re.S)
# -m / --message, also in a short-flag group (`-am`, `-qam`); value attached or not
_MSG_ARG_RE = re.compile(
    r"""(?<!\S)(?:-(?!-)[a-zA-Z]*m|--message)(?:=|\s*)(?:"((?:[^"\\]|\\.)*)"|'([^']*)'|(\S+))""")
_NO_SUBJECT_RE = re.compile(r"(?<!\S)(?:-[a-zA-Z]*[FCct]\b|--(?:file|template|reuse-message|reedit-message|fixup|squash)\b)")

from .when import command_words, shell_segments  # noqa: E402


def commit_segments(cmd):
    """The `git commit` commands of a shell line, each alone (its own segment with
    its heredoc message), found where a command starts — after assignments and
    keywords (`HUSKY=0 git commit`, `if git commit`, `(git commit`). Text that only
    mentions `git commit` (a heredoc body, an echo) is not a commit."""
    return [command_words(s) for s in shell_segments(cmd) if COMMIT_RE.match(command_words(s))]


def commit_subject(segment):
    """Subject of the first message flag of one `git commit` segment; None — cannot
    read it (-F/-C/-c/-t, --fixup, editor, no -m, a variable)."""
    m_commit = COMMIT_RE.match(segment or "")
    if not m_commit:
        return None
    args = segment[m_commit.end():]
    m = _MSG_ARG_RE.search(args)
    if not m or _NO_SUBJECT_RE.search(args[:m.start()]):
        return None
    if m.group(1) is not None and m.group(1).lstrip().startswith("$(cat"):
        h = _HEREDOC_MSG_RE.search(args, m.start())
        return h.group(2).strip() if h else None
    text = next(g for g in m.groups() if g is not None)
    subject = text.replace('\\"', '"').split("\n", 1)[0].strip()
    return None if "$" in subject else subject


def scope_texts(scope, call):
    if scope == "command":
        return [call.command] if call.command else []
    if scope == "commit-message":
        return commit_segments(call.command)
    if scope == "commit-subject":
        return [s for s in (commit_subject(seg) for seg in commit_segments(call.command)) if s is not None]
    if scope == "content":
        t = call.new_text()
        return [t] if t else []
    return []


# ------------------------------------------------------------------ data
def kind_of(g):
    kinds = [k for k in DECISIONS if k in g]
    return kinds[0] if len(kinds) == 1 else None


def guard_id(rule, i, g):
    return str(g.get("id") or f"{rule.rel}#{i + 1}")


def applies(g, call, home, rule):
    """The guard's scope has something to look at in this call."""
    if "paths" in g and g.get("in") != "paths":
        if not any(paths_match(g["paths"], p, home, rule.repo) for p in call.targets):
            return False
    scope = g.get("in", "command")
    if scope == "paths":
        return bool(call.targets)
    return bool(scope_texts(scope, call))


def violation(g, call, home, rule):
    """What violates the guard in this call ("" — something did, without a text to
    quote), or None."""
    kind = kind_of(g)
    if kind is None or not applies(g, call, home, rule):
        return None
    pattern, scope = g[kind], g.get("in", "command")
    unless = kind.endswith("-unless")
    if scope == "paths":
        hits = [p for p in call.targets if paths_match(pattern, p, home, rule.repo)]
        if unless:
            miss = [p for p in call.targets if p not in hits]
            return miss[0] if miss else None
        return hits[0] if hits else None
    try:
        rx = re.compile(str(pattern), re.M)
    except re.error:
        return None
    for text in scope_texts(scope, call):
        m = rx.search(text)
        if unless and not m:
            return text.split("\n", 1)[0]
        if not unless and m:
            return m.group(0)
    return None


def evaluate(rules, call, session):
    """All decisions on the call: data guards of in-scope rules, then live
    extensions. Rules must already be filtered to the session's scope."""
    out = []
    for r in rules:
        for i, g in enumerate(r.guard):
            found = violation(g, call, session.home, r)
            if found is None:
                continue
            kind = kind_of(g).removesuffix("-unless")
            out.append(Decision(kind, guard_id(r, i, g), msg.guard_reason(g.get("why"), r.rel, found), r))
    ext.load(session.home)
    for cid, check in ext.LIVE.items():
        try:
            res = check.fn(call, session)
        except Exception as e:   # noqa: BLE001 — a broken check must not block work
            out.append(Decision("ask", cid, f"desk: проверка {cid} упала: {e!r}"))
            continue
        if isinstance(res, Decision):
            out.append(res)
        elif res:
            kind, reason = res[0], res[1]
            rule = res[2] if len(res) > 2 else None
            out.append(Decision(kind, cid, reason, rule))
    if no_deny():
        out = [d for d in out if d.kind != "deny"]
    return out


def no_deny():
    return bool(os.environ.get(NO_DENY_ENV))


def decide(rules, call, session, state):
    """The decision for PreToolUse: any deny wins; else the first ask not yet asked."""
    found = evaluate(rules, call, session)
    deny = [d for d in found if d.kind == "deny"]
    if deny:
        return deny[0]
    asks = [d for d in found if d.kind == "ask" and d.id not in state.asks]
    return asks[0] if asks else None


def load_errors(home):
    return [msg.ext_error(name, err) for name, err in ext.load(home)]


def added_text(path):
    """Text a change added to a file: diff against the index for a tracked file,
    the whole file for a new one."""
    p = Path(path)
    root = homelib.checkout_root(str(p))
    if root:
        try:
            diff = subprocess.run(["git", "-C", str(root), "diff", "-U0", "--", str(p)],
                                  capture_output=True, text=True, timeout=5).stdout
            if diff:
                return "\n".join(l[1:] for l in diff.splitlines()
                                 if l.startswith("+") and not l.startswith("+++"))
        except Exception:
            pass
    try:
        return p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def lint_guard(g):
    """Problems of one guard entry: [message]."""
    probs = []
    if not isinstance(g, dict):
        return ["guard entry is not a mapping"]
    unknown = [k for k in g if k not in KEYS]
    if unknown:
        probs.append(f"unknown guard keys: {', '.join(unknown)}")
    kinds = [k for k in DECISIONS if k in g]
    if len(kinds) != 1:
        probs.append(f"guard needs exactly one of {', '.join(DECISIONS)}")
    scope = g.get("in", "command")
    if scope not in SCOPES:
        probs.append(f"unknown guard scope `{scope}` (one of {', '.join(SCOPES)})")
    if kinds and scope != "paths":
        try:
            re.compile(str(g[kinds[0]]))
        except re.error as e:
            probs.append(f"bad regex in {kinds[0]}: {e}")
    if not g.get("why"):
        probs.append("guard without `why`: the model gets no reason")
    return probs
