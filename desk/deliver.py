"""Delivery: tiers, budget, cutting and per-session state.

A hook output longer than ~10k characters is hidden by the harness in a persisted
file with a ~2 KB preview, so everything is delivered in portions of at most
BUDGET bytes. A rule larger than that is cut by `##` sections into labelled parts
("rule.md (часть 2 из 3)"); a section larger than the budget — by lines.

State per session (one JSON file under the state dir, guarded by a lock): which
units are already in context, what is queued (did not fit), which gates and asks
were already used. `clear`/`compact` wipe the context, so they wipe the state too.
"""
import contextlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import frontmatter

BUDGET = 12 * 1024
STATE_ENV = "DESK_STATE_DIR"
LOCK_WAIT = 3.0
CONTEXT_RESET_SOURCES = ("clear", "compact")
CONTEXT_KEPT_SOURCES = ("resume", "fork")
SOURCE_KEYS = ("source", "session_start_reason")


@dataclass
class Unit:
    rel: str
    label: str
    text: str

    @property
    def size(self):
        return len(self.text.encode("utf-8"))


def _blocks(body):
    """Body split before every `## ` heading (outside code fences)."""
    out, cur, fence = [], [], False
    for line in body.splitlines():
        if line.lstrip().startswith("```"):
            fence = not fence
        if line.startswith("## ") and cur and not fence:
            out.append("\n".join(cur))
            cur = [line]
        else:
            cur.append(line)
    if cur:
        out.append("\n".join(cur))
    return out


def _size(s):
    return len(s.encode("utf-8"))


def _pack(blocks, budget):
    chunks, cur = [], ""
    for b in blocks:
        if _size(b) > budget:
            if cur:
                chunks.append(cur)
                cur = ""
            for line in b.splitlines():
                nxt = f"{cur}\n{line}" if cur else line
                if _size(nxt) > budget and cur:
                    chunks.append(cur)
                    cur = line
                else:
                    cur = nxt
            continue
        nxt = f"{cur}\n\n{b}" if cur else b
        if _size(nxt) > budget and cur:
            chunks.append(cur)
            cur = b
        else:
            cur = nxt
    if cur:
        chunks.append(cur)
    return chunks


def units(rule, budget=BUDGET):
    """Units of a rule file: one if its body fits the budget, else labelled parts."""
    body = frontmatter.body(rule.text())
    if _size(body) <= budget:
        return [Unit(rule.rel, rule.rel, body)]
    chunks = _pack(_blocks(body), budget)
    return [Unit(rule.rel, f"{rule.rel} (часть {i} из {len(chunks)})", c)
            for i, c in enumerate(chunks, 1)]


def order_action(fired):
    """Narrow before broad: a rule with one `when:` entry is about exactly this
    action, a many-entry one is background; then repository → context → global
    (the mechanics of an action live closest to the work), then size. The budget
    cuts the tail, so the first portion must carry the mechanics."""
    return sorted(fired, key=lambda f: (len(f.rule.when), f.rule.layer, f.rule.path.stat().st_size
                                        if f.rule.path.exists() else 0, f.rule.rel))


def bundle(rules, state, budget=BUDGET):
    """Units of the rules not yet delivered, within the budget: (send, rest)."""
    send, rest, total, seen = [], [], 0, set()
    for r in rules:
        for u in units(r, budget):
            if u.label in state.delivered or u.label in seen:
                continue
            seen.add(u.label)
            if send and total + u.size > budget:
                rest.append(u)
                continue
            send.append(u)
            total += u.size
    return send, rest


def body_of(units_):
    return "".join(f"\n--- {u.label} ---\n{u.text}" for u in units_)


def kb(n):
    return f"{max(1, round(n / 1024))} КБ"


def sized(units_):
    return ", ".join(f"{u.label} {kb(u.size)}" for u in units_)


# --------------------------------------------------------------------- state
def state_dir():
    env = os.environ.get(STATE_ENV)
    if env:
        return Path(env)
    return Path(os.environ.get("TMPDIR", "/tmp")) / "desk-core"


@dataclass
class State:
    session_id: str = ""
    delivered: set = field(default_factory=set)   # unit labels in context
    pending: list = field(default_factory=list)   # rule rels queued for the next call
    gates: set = field(default_factory=set)       # rule rels whose gate was used
    asks: set = field(default_factory=set)        # guard ids already confirmed once
    notes: set = field(default_factory=set)       # one-time hints already given

    @property
    def file(self):
        return state_dir() / f"{self.session_id}.json" if self.session_id else None

    @classmethod
    def load(cls, session_id):
        s = cls(session_id=session_id or "")
        f = s.file
        if f is None:
            return s
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return s
        s.delivered = set(raw.get("delivered") or [])
        s.pending = list(raw.get("pending") or [])
        s.gates = set(raw.get("gates") or [])
        s.asks = set(raw.get("asks") or [])
        s.notes = set(raw.get("notes") or [])
        return s

    def save(self):
        f = self.file
        if f is None:
            return
        data = {"delivered": sorted(self.delivered), "pending": self.pending,
                "gates": sorted(self.gates), "asks": sorted(self.asks), "notes": sorted(self.notes)}
        tmp = f.with_name(f"{f.name}.{os.getpid()}.tmp")
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, f)    # a reader without the lock sees old or new, never empty
        except OSError:
            pass

    def sent(self, units_, rest=()):
        """Mark units delivered; queue the rules of what did not fit."""
        self.delivered |= {u.label for u in units_}
        done = {u.rel for u in units_}
        self.pending = list(dict.fromkeys([r for r in self.pending if r not in done]
                                          + [u.rel for u in rest]))

    def fully_delivered(self, rule, budget=BUDGET):
        return all(u.label in self.delivered for u in units(rule, budget))


RESET_WINDOW = 30   # seconds: the slots of one start run together


SNAP_MAX_AGE = 24 * 3600


def sweep_snapshots(now=None):
    """Snapshots of calls that never ran (denied, an ask the human rejected) have no
    PostToolUse to read them: drop the ones older than a day."""
    now = time.time() if now is None else now
    for f in state_dir().glob("*.snap.json") if state_dir().is_dir() else []:
        try:
            if now - f.stat().st_mtime > SNAP_MAX_AGE:
                f.unlink()
        except OSError:
            pass


def reset(data, now=None):
    """`clear`/`compact` empty the context; the state must follow, or rules would
    count as delivered while missing. Every start slot calls it (under the session
    lock): the first one resets and leaves a mark, the others see a fresh mark and
    keep what the first slots already delivered. True — reset done."""
    data = data or {}
    sid = data.get("session_id")
    if not sid or not any(data.get(k) in CONTEXT_RESET_SOURCES for k in SOURCE_KEYS):
        return False
    mark = state_dir() / f"{sid}.reset"
    now = time.time() if now is None else now
    try:
        if now - mark.stat().st_mtime < RESET_WINDOW:
            return False
    except OSError:
        pass
    try:
        (state_dir() / f"{sid}.json").unlink()
    except OSError:
        pass
    try:
        state_dir().mkdir(parents=True, exist_ok=True)
        mark.write_text(str(now))
        os.utime(mark, (now, now))
    except OSError:
        pass
    return True


def context_kept(data):
    """resume/fork bring the old transcript back, start deliveries included: a second
    copy costs input and adds nothing."""
    return any((data or {}).get(k) in CONTEXT_KEPT_SOURCES for k in SOURCE_KEYS)


@contextlib.contextmanager
def session_lock(session_id, wait=LOCK_WAIT):
    """Lock on the session state: parallel calls of one turn would otherwise read the
    same state and deliver a rule twice. Timeout — go without the lock: a duplicate
    beats a stuck action."""
    import fcntl
    fd = held = None
    if session_id:
        try:
            state_dir().mkdir(parents=True, exist_ok=True)
            fd = os.open(state_dir() / f"{session_id}.lock", os.O_CREAT | os.O_RDWR, 0o644)
            deadline = time.monotonic() + wait
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    held = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(0.02)
        except OSError:
            pass
    try:
        yield
    finally:
        if fd is not None:
            if held:
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


# ------------------------------------------------------------- session slots
def portions(rules, budget=BUDGET):
    """({rel: number of the portion it starts in}, portions) — the packing the hook
    does for these rules, call after call."""
    state, rows, n = State(""), {}, 0
    while True:
        send, rest = bundle(rules, state, budget)
        if not send:
            return rows, n
        n += 1
        for u in send:
            rows.setdefault(u.rel, n)
        state.sent(send, rest)


def partition(rules, slots, budget=BUDGET):
    """Session-tier units spread over `slots` outputs, greedy and deterministic;
    what fits nowhere is listed by path. A late small part may land in an earlier
    slot than its first part: all slots arrive before the first action anyway."""
    buckets = [[] for _ in range(slots)]
    sizes = [0] * slots
    rest = []
    for r in rules:
        for u in units(r, budget):
            for i in range(slots):
                if sizes[i] + u.size <= budget:
                    buckets[i].append(u)
                    sizes[i] += u.size
                    break
            else:
                rest.append(r)
                break
    return buckets, rest
