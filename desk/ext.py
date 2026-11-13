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
"""
import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path

from .home import desk_home  # noqa: F401 — re-exported for extensions
from .when import Call, glob_match, paths_match, write_targets  # noqa: F401


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
_loaded = {}


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
    from . import checks  # noqa: F401 — registers the universal checks
    home = Path(home or desk_home())
    key = str(home)
    if key in _loaded:
        return _loaded[key]
    errors = []
    for f in sorted((home / "ext").glob("*.py")) if (home / "ext").is_dir() else []:
        name = f"desk_ext_{abs(hash(key)) % 10**8}_{f.stem}"
        try:
            spec = importlib.util.spec_from_file_location(name, f)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            spec.loader.exec_module(mod)
        except Exception as e:   # noqa: BLE001 — anything an extension raises
            errors.append((f.name, repr(e)))
    _loaded[key] = errors
    return errors
