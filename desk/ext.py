"""Extension API: Python checks that data cannot express.

An extension is a module in `DESK_HOME/ext/*.py`:

    from desk import ext

    @ext.live("no-prod-ssh", "ssh only to staging hosts")
    def no_prod_ssh(call, session):
        if call.tool == "Bash" and "ssh prod" in call.command:
            return ("deny", "desk: prod is read-only — ask the human")

    @ext.compliance("no-prod-ssh", since="2026-11-01")
    def no_prod_ssh_cases(record, home):
        return [(not "ssh prod" in ev["input"].get("command", ""), "ssh")
                for ev in record["events"] if ev["kind"] == "tool_use" and ev["name"] == "Bash"]

`live(call, session)` runs in PreToolUse (and on actual changes in PostToolUse,
where it can only remark) and returns None or (`deny`|`ask`, reason).
`compliance(record, home)` runs over transcripts (`desk measure`) and returns
[(ok, what)] — one case per situation the rule applies to.
The engine's own universal checks are registered the same way (desk.checks).
Code shared by extensions lives in a package under `ext/` (only `ext/*.py` are
loaded as extensions); `ext/` is on sys.path, so `from mylib import x` works.
"""
import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path

from .home import desk_home  # re-exported for extensions
from .when import Call, glob_match, paths_match, write_targets


@dataclass
class Check:
    id: str
    label: str
    fn: object
    since: str = None
    source: str = "core"


LIVE = {}
COMPLIANCE = {}
LINT = {}
EVENTS = {}
_loaded = {}


@dataclass
class Event:
    """What an event handler gets. `state` — the session's delivery state; a handler
    that says something once per session keeps its keys in `state.notes`.
    `changed` — files a Bash command really changed (PostToolUse)."""
    name: str
    data: dict
    session: object
    call: object = None
    state: object = None
    changed: tuple = ()
    header: object = None   # SessionStart: desk.header.Header to add rows/signals to (None — off)


def on(event):
    """`fn(ev: Event) -> str | None` for SessionStart, UserPromptSubmit, PreToolUse,
    PostToolUse or SessionEnd: the text goes to the model with the hook's output
    (SessionStart — after the context line; SessionEnd output is dropped). For what
    the engine does not do itself: a session header, checkout state, a journal."""
    def wrap(fn):
        EVENTS.setdefault(event, []).append(fn)
        return fn
    return wrap


def emit(event, ev):
    """Texts of the event's handlers, in registration order; a crashing handler
    yields an error line instead of breaking the hook."""
    out = []
    for fn in EVENTS.get(event, []):
        try:
            text = fn(ev)
        except Exception as e:
            text = f"[desk] обработчик {getattr(fn, '__name__', fn)} ({event}) упал: {e!r}"
        if text:
            out.append(str(text))
    return out


IN_WORK = []
CHECKOUTS = []


def checkouts(fn):
    """`fn(session) -> [path]` — checkouts of the machine the worktree sweep and the
    change_directory hint know besides the session's own (a registry of repos, say)."""
    CHECKOUTS.append(fn)
    return fn


def checkouts_of(session):
    out = []
    for fn in CHECKOUTS:
        try:
            out += [p for p in fn(session) or [] if p not in out]
        except Exception:
            pass
    return out


def in_work(fn):
    """`fn(names, context) -> True | False | None` — is the work on a branch alive (a
    tracker status of its task, say). `names` — the branch and, for a worktree, its
    folder; `context` — the place of the checkout (desk.home.Context or None). Used by
    checkout busy and worktree verdicts."""
    IN_WORK.append(fn)
    return fn


def in_work_of(names, context):
    """True when any resolver says so; False when one says no and none says yes;
    None — nobody knows."""
    seen = None
    for fn in IN_WORK:
        try:
            r = fn(tuple(names), context)
        except Exception:
            r = None
        if r is True:
            return True
        if r is False:
            seen = False
    return seen


def live(id, label=""):
    def wrap(fn):
        LIVE[id] = Check(id, label or id, fn, source=_source(fn))
        return fn
    return wrap


def compliance(id, label="", since=None):
    def wrap(fn):
        COMPLIANCE[id] = Check(id, label or id, fn, since, source=_source(fn))
        return fn
    return wrap


def lint(id, label=""):
    """`fn(home) -> [(path, line, message)]` — a structural check for `desk lint`."""
    def wrap(fn):
        LINT[id] = Check(id, label or id, fn, source=_source(fn))
        return fn
    return wrap


def _source(fn):
    mod = getattr(fn, "__module__", "") or ""
    return "core" if mod.startswith("desk.") else mod


def load(home=None):
    """Import the core checks and `<home>/ext/*.py` once per process; errors —
    [(file, message)] (a broken extension must not break the hook)."""
    from . import checkout, checks, journal, threads, trees  # registers the universal checks and handlers
    home = Path(home or desk_home())
    key = str(home)
    if key in _loaded:
        return _loaded[key]
    errors = []
    if (home / "ext").is_dir() and str(home / "ext") not in sys.path:
        sys.path.insert(0, str(home / "ext"))   # shared code of extensions: ext/<pkg>/
    for f in sorted((home / "ext").glob("*.py")) if (home / "ext").is_dir() else []:
        name = f"desk_ext_{abs(hash(key)) % 10**8}_{f.stem}"
        try:
            spec = importlib.util.spec_from_file_location(name, f)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            spec.loader.exec_module(mod)
        except Exception as e:   # anything an extension raises
            errors.append((f.name, repr(e)))
    _loaded[key] = errors
    return errors
