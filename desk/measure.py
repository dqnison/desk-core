"""Reach and compliance over transcripts — measured by result, not by "file opened".

reach       — of the sessions where a rule's `when:` fired, in how many the rule
              reached the model (delivered by a hook or read in full)
compliance  — share of cases where a check holds: extension checks
              (desk.ext.compliance) plus checks derived from data guards
weight      — bytes of input each tier costs (no transcripts needed)
repeats     — extra deliveries: a file given again in one stretch of a session, or
              given after the session had read it itself

Sessions inside DESK_HOME are left out of reach (rules are edited there, not
applied) but not out of compliance.
"""
import datetime
import os
import re
from collections import defaultdict
from pathlib import Path

from . import deliver as dl, ext, guards, home as homelib, when as wh
from .transcripts import iter_session, transcripts


def _delivered(rule, events, upto):
    """The rule's text was in context before event `upto`: a hook attached it
    (its label in an attachment) or the file was read with Read."""
    path, found = str(rule.path), False
    for ev in events[:upto]:
        if ev["kind"] == "compact":          # the context is empty again
            found = False
        elif ev["kind"] == "attachment" and f"--- {rule.rel}" in ev["text"]:
            found = True
        elif (ev["kind"] == "tool_use" and ev["name"] == "Read"
              and (ev.get("input") or {}).get("file_path") == path):
            found = True
    return found


def _call(ev, cwd):
    if ev["kind"] == "user_text":
        return wh.Call(prompt=ev["text"], cwd=cwd or ".")
    return wh.Call(tool=ev["name"], input=ev.get("input") or {}, cwd=cwd or ".")


class Sessions:
    """Session objects per transcript cwd, cached: remotes are read once."""

    def __init__(self, home):
        self.home = Path(home)
        self.cache = {}

    def of(self, cwd):
        cwd = cwd or "."
        if cwd not in self.cache:
            self.cache[cwd] = homelib.session_from({"cwd": cwd}, self.home, project_dir="")
        return self.cache[cwd]


def reach(rules, records, home):
    """{rule rel: (fired sessions, reached sessions)}."""
    sessions = Sessions(home)
    out = defaultdict(lambda: [0, 0])
    candidates = [r for r in rules if r.tier in ("session", "action") and r.when and not r.error]
    for rec in records:
        s = sessions.of(rec["cwd"])
        if s.in_home:
            continue
        events = rec["events"]
        first = {}
        for i, ev in enumerate(events):
            if ev["kind"] not in ("tool_use", "user_text"):
                continue
            call = _call(ev, rec["cwd"])
            repos = wh.repos_touched(call, s) if not call.is_prompt else {s.repo}
            scoped = [r for r in candidates if r.rel not in first and homelib.in_scope(r, s, repos)]
            for f in wh.fired(scoped, call, home, s.context_name):
                first[f.rule.rel] = (f.rule, i)
        for rel, (rule, i) in first.items():
            out[rel][0] += 1
            # delivered before the call or with it: the hook's attachment is stored
            # after the tool_use record, before the next call
            upto = next((j for j in range(i + 1, len(events)) if events[j]["kind"] == "tool_use"),
                        len(events))
            out[rel][1] += _delivered(rule, events, upto)
    return {k: tuple(v) for k, v in out.items()}


def trigger_reach(rules, records, home):
    """{named trigger: (fired sessions, reached sessions)}: of the sessions where a
    trigger of desk.yml fired, in how many a rule naming it reached the model before
    the next call. Only rules in the session's scope count; triggers that never
    fired are absent."""
    sessions = Sessions(home)
    out = defaultdict(lambda: [0, 0])
    candidates = [r for r in rules if r.tier in ("session", "action") and r.when and not r.error]
    for rec in records:
        s = sessions.of(rec["cwd"])
        if s.in_home:
            continue
        named = wh.named_triggers(home, s.context_name)
        events, first = rec["events"], {}
        for i, ev in enumerate(events):
            if ev["kind"] not in ("tool_use", "user_text"):
                continue
            call = _call(ev, rec["cwd"])
            for n, entries in named.items():
                if n not in first and any(wh.match_entry(e, call, home) for e in entries):
                    first[n] = (i, call)
        for n, (i, call) in first.items():
            repos = wh.repos_touched(call, s) if not call.is_prompt else {s.repo}
            files = [r for r in candidates if n in r.when and homelib.in_scope(r, s, repos)]
            upto = next((j for j in range(i + 1, len(events)) if events[j]["kind"] == "tool_use"),
                        len(events))
            out[n][0] += 1
            out[n][1] += any(_delivered(r, events, upto) for r in files)
    return {k: tuple(v) for k, v in out.items()}


LABEL_RE = re.compile(r"^--- (\S+\.md(?: \(часть \d+ из \d+\))?) ---$", re.M)


def repeats(records, home):
    """Extra deliveries of a file within one session, between compactions: `copies` —
    a hook gave the same label again from another event; `after_read` — a hook gave a
    file the session had already read (Read or cat/sed -n/head/tail).
    [{path, sessions, copies, after_read, extra_bytes}], most repeated first."""
    home = Path(home)
    by = {}
    for rec in records:
        seen, read, counts = {}, set(), {}
        for ev in rec["events"]:
            if ev["kind"] == "compact":
                seen, read = {}, set()
                continue
            if ev["kind"] == "tool_use":
                inp = ev.get("input") or {}
                if ev["name"] == "Read":
                    read.add(os.path.normpath(str(inp.get("file_path", ""))))
                elif ev["name"] == "Bash":
                    read.update(wh.read_targets(str(inp.get("command", "")), ev.get("cwd") or rec.get("cwd") or "."))
                continue
            if ev["kind"] != "attachment":
                continue
            for lab in set(LABEL_RE.findall(ev["text"])):
                path = lab.split(" (часть", 1)[0]
                parts = re.search(r"из (\d+)\)$", lab)
                c = counts.setdefault(path, {"copies": 0, "after_read": 0, "bytes": 0})
                try:
                    unit = (home / path).stat().st_size // (int(parts.group(1)) if parts else 1)
                except OSError:
                    unit = 0
                if lab in seen:
                    if seen[lab] != ev.get("delivery"):
                        c["copies"] += 1
                        c["bytes"] += unit
                    continue
                if str(home / path) in read:
                    c["after_read"] += 1
                    c["bytes"] += unit
                seen[lab] = ev.get("delivery")
        for path, c in counts.items():
            if c["copies"] or c["after_read"]:
                d = by.setdefault(path, {"path": path, "sessions": 0, "copies": 0, "after_read": 0,
                                         "extra_bytes": 0})
                d["sessions"] += 1
                d["copies"] += c["copies"]
                d["after_read"] += c["after_read"]
                d["extra_bytes"] += c["bytes"]
    return sorted(by.values(), key=lambda d: -(d["copies"] + d["after_read"]))


def guard_checks(rules):
    """Compliance checks derived from data guards: [(id, label, since, fn)]."""
    out = []
    for r in rules:
        for i, g in enumerate(r.guard):
            if guards.kind_of(g) is None:
                continue
            gid = guards.guard_id(r, i, g)
            out.append((gid, g.get("why") or gid, g.get("since") or r.fm.get("since"),
                        _guard_fn(r, i, g)))
    return out


def _guard_fn(rule, i, g):
    asking = guards.kind_of(g).startswith("ask")

    def cases(record, home, session):
        if not homelib.in_scope(rule, session, None if rule.repo is None else {rule.repo}):
            return []
        res = []
        for n, ev in enumerate(record["events"]):
            if ev["kind"] != "tool_use":
                continue
            call = _call(ev, record["cwd"])
            if not guards.applies(g, call, home, rule):
                continue
            found = guards.violation(g, call, home, rule)
            if asking:
                if found is not None:
                    res.append((_delivered(rule, record["events"], n), str(found)[:160]))
            else:
                res.append((found is None, (str(found) if found else call.command or call.tool)[:160]))
        return res
    return cases


def _since_ok(record, since):
    if not since:
        return True
    return (record.get("started") or "") >= str(since)


def compliance(rules, records, home):
    """{check id: (label, ok, total, [failed what])}."""
    ext.load(home)
    sessions = Sessions(home)
    checks = [(c.id, c.label, c.since, lambda rec, h, s, fn=c.fn: fn(rec, h))
              for c in ext.COMPLIANCE.values()]
    checks += guard_checks([r for r in rules if not r.error])
    out = {}
    for cid, label, since, fn in checks:
        ok = total = 0
        failed = []
        for rec in records:
            if not _since_ok(rec, since):
                continue
            try:
                cases = fn(rec, home, sessions.of(rec["cwd"]))
            except Exception as e:
                cases = [(False, f"check crashed: {e!r}")]
            for good, what in cases:
                total += 1
                ok += bool(good)
                if not good:
                    failed.append(what)
        out[cid] = (label, ok, total, failed)
    return out


def weight(rules):
    """{tier: bytes} of rule bodies."""
    out = defaultdict(int)
    for r in rules:
        if r.tier in homelib.TIERS:
            out[r.tier] += len(r.body().encode("utf-8"))
    return dict(out)


def load_records(root=None, days=28, session=None):
    since = None
    if days:
        since = (datetime.datetime.now() - datetime.timedelta(days=days)).timestamp()
    return [iter_session(f) for f in transcripts(root, since, session)]


def report(home, root=None, days=28, examples=3):
    rules = [r for r in homelib.load_rules(home) if r.enabled]
    records = load_records(root, days)
    lines = [f"Сессий: {len(records)} (окно {days} дн)", "", "Вес по ярусам:"]
    for tier, n in sorted(weight(rules).items()):
        lines.append(f"  {tier:8} {dl.kb(n)}")
    lines += ["", "Досягаемость (сработал when: → правило дошло):"]
    for rel, (fired, reached) in sorted(reach(rules, records, home).items()):
        lines.append(f"  {rel:50} {reached}/{fired}" + (f"  {round(100 * reached / fired)} %" if fired else ""))
    lines += ["", "Соблюдение:"]
    for cid, (label, ok, total, failed) in sorted(compliance(rules, records, home).items()):
        share = f"{round(100 * ok / total)} %" if total else "—"
        lines.append(f"  {cid:50} {ok}/{total} {share}  {label}")
        lines += [f"      ✗ {w}" for w in failed[:examples]]
    return "\n".join(lines)
