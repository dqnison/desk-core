"""One hook entry for every event: dispatch on `hook_event_name`.

    SessionStart      session-tier rules as content, spread over slots (`--slot N --slots K`,
                      one registration per slot = one output each); reset on clear/compact
    UserPromptSubmit  rules whose `when: prompt` matches the user's prompt
    PreToolUse        guards (deny/ask, the only thing that acts before the call), gates,
                      action-tier rules of the fired `when:` entries, the queue
    PostToolUse       files read in full count as delivered; for Bash — what the command
                      really changed (`git status` before/after): `paths:` rules and guard
                      remarks on actual changes
    SessionEnd        extension handlers (the session journal)
    WorktreeCreate    the session's worktree from the place's integration branch
                      (desk.worktree); runs even with the hooks quiet — the harness waits

The command is never rewritten. No model calls.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from . import deliver as dl
from . import ext, guards, header as hdr, home as homelib, messages as msg, when as wh


def hook_json(event, context="", message="", **fields):
    spec = {**fields, **({"additionalContext": context} if context else {})}
    out = {"hookSpecificOutput": {"hookEventName": event, **spec}} if spec else {}
    if message:
        out["systemMessage"] = message
    return json.dumps(out, ensure_ascii=False) if out else ""


def main(argv=None, stdin=None):
    ap = argparse.ArgumentParser(prog="desk-hook")
    ap.add_argument("--slot", type=int, default=1)
    ap.add_argument("--slots", type=int, default=1)
    a = ap.parse_args(argv)
    try:
        data = json.load(stdin or sys.stdin)
    except ValueError:
        return 0
    if not isinstance(data, dict):
        return 0
    if data.get("hook_event_name") == "WorktreeCreate":     # the harness waits for a path, even quiet
        from . import worktree
        return worktree.run(data)
    if homelib.quiet():
        return 0
    try:
        out = handle(data, a.slot, a.slots)
    except Exception as e:    # a broken hook must never block the user's work
        out = hook_json(data.get("hook_event_name") or "", message=f"{msg.MARK} hook error: {e!r}")
    if out:
        print(out)
    return 0


def handle(data, slot=1, slots=1):
    event = data.get("hook_event_name") or ""
    if event == "SessionStart":
        return session_start(data, slot, slots)
    if event == "UserPromptSubmit":
        return prompt(data)
    if event == "PreToolUse":
        return pre_tool(data)
    if event == "PostToolUse":
        return post_tool(data)
    if event == "SessionEnd":
        session = homelib.session_from(data)
        ext.load(session.home)
        ext.emit("SessionEnd", ext.Event("SessionEnd", data, session))
    return ""


# -------------------------------------------------------------------- common
def _rules(session):
    return [r for r in homelib.load_rules(session.home) if not r.error and r.enabled]


def deliverable(rules, session, repos):
    """In-scope rules whose `when:` may deliver them: tiers session and action
    (import is always in context, link has no carrier)."""
    return [r for r in rules if r.tier in ("session", "action") and homelib.in_scope(r, session, repos)]


def session_rules(rules, session):
    """Session tier for this session: global, the active context, the session repo;
    the context hub first."""
    hub = session.context.hub if session.context else None
    picked = [r for r in rules if r.tier == "session" and homelib.in_scope(r, session)
              and not homelib.skipped(r, session)]
    return sorted(picked, key=lambda r: (r.path != hub, r.layer, r.rel))


def newcomer_rules(rules, session, repos):
    """Session-tier rules of repositories the call touches besides the session's:
    the start did not deliver them."""
    others = repos - {session.repo}
    return [r for r in rules if r.tier == "session" and r.repo in others
            and homelib.in_scope(r, session, repos) and not homelib.skipped(r, session)]


def _by_rel(rules, rels):
    idx = {r.rel: r for r in rules}
    return [idx[r] for r in rels if r in idx]


# -------------------------------------------------------------- SessionStart
def session_start(data, slot, slots):
    """Slot 0 — the context line and extension texts (a header can be large, so it
    gets an output of its own); slots 1..K — session-tier files. A single
    registration (`--slots 1`) does both in one output. Every slot may reset the state on clear/compact:
    the first one to take the lock does it, the rest see it fresh."""
    session = homelib.session_from(data)
    with dl.session_lock(session.session_id):
        dl.reset(data)
    if dl.context_kept(data):
        return ""
    out = []
    if slot == 0 or slots == 1:
        out.append(msg.context_line(session.context_name, session.basis, session.home))
        out += guards.load_errors(session.home)
        scratch = dl.State.load(session.session_id)
        h = hdr.new(_rules(session), session) if hdr.enabled(session.home) else None
        out += ext.emit("SessionStart", ext.Event("SessionStart", data, session, state=scratch, header=h))
        if h is not None:
            out.append(hdr.render(h))
        with dl.session_lock(session.session_id):
            state = dl.State.load(session.session_id)
            state.notes |= scratch.notes
            state.save()
        dl.sweep_snapshots()
        if slot == 0:
            return hook_json("SessionStart", "\n".join(out), msg.summary("SessionStart", out[0]))
    rules = _rules(session)
    files = session_rules(rules, session)
    buckets, rest = dl.partition(files, slots)
    used = sum(1 for b in buckets if b) or 1
    mine = buckets[slot - 1] if 1 <= slot <= slots else []
    last = min(slots, used)
    if slot == 1 and not files:
        out.append(msg.no_session_files())
    if mine:
        out.append(msg.slot_head(slot, used, [u.label for u in mine]) + dl.body_of(mine))
    if rest and slot == last:
        out.append(msg.slot_rest([str(r.path) for r in rest]))
    if mine:
        with dl.session_lock(session.session_id):
            state = dl.State.load(session.session_id)
            state.sent(mine)
            state.save()
    if not out:
        return ""
    return hook_json("SessionStart", "\n".join(out), msg.summary(
        "SessionStart", f"слот {slot} из {used} · ярус session · {dl.sized(mine) or 'пусто'}",
        rest and slot == last and "не влезло в слоты: " + ", ".join(r.rel for r in rest)))


# ---------------------------------------------------------- UserPromptSubmit
def prompt(data):
    session = homelib.session_from(data)
    call = wh.Call.from_hook(data)
    rules = deliverable(_rules(session), session, {session.repo})
    fired = dl.order_action(wh.fired(rules, call, session.home, session.context_name))
    ext.load(session.home)
    with dl.session_lock(session.session_id):
        state = dl.State.load(session.session_id)
        notes = "\n".join(ext.emit("UserPromptSubmit", ext.Event("UserPromptSubmit", data, session, call, state)))
        out = _deliver(fired, [], state, "UserPromptSubmit", prefix=notes) if fired else ""
        if notes and not out:
            out = hook_json("UserPromptSubmit", notes)
        state.save()
    return out


def _reasons(fired):
    return "; ".join(f"{f.rule.rel} ← {f.why}" for f in fired)


def _deliver(fired, extra, state, event, prefix=""):
    rules = list(dict.fromkeys([f.rule for f in fired] + extra))
    rules = list({r.rel: r for r in rules}.values())
    send, rest = dl.bundle(rules, state)
    if not send:
        return ""
    sent_rels = {u.rel for u in send}
    reasons = _reasons([f for f in fired if f.rule.rel in sent_rels]) or "очередь прошлых порций"
    head = msg.action_head(reasons, [u.label for u in send], event, [u.label for u in rest],
                           dl.revised(send, state))
    state.sent(send, rest)
    text = (prefix + "\n" if prefix else "") + head + dl.body_of(send)
    return hook_json(event, text, msg.summary(event, f"подано {dl.sized(send)} · ярус action",
                                              rest and "остаток — следующим вызовом"))


# ---------------------------------------------------------------- PreToolUse
def pre_tool(data):
    session = homelib.session_from(data)
    call = wh.Call.from_hook(data)
    repos = wh.repos_touched(call, session)
    all_rules = _rules(session)
    scoped = [r for r in all_rules if homelib.in_scope(r, session, repos)]
    fired = dl.order_action(wh.fired(deliverable(all_rules, session, repos), call, session.home,
                                     session.context_name))
    newcomers = newcomer_rules(all_rules, session, repos)
    with dl.session_lock(session.session_id):
        state = dl.State.load(session.session_id)
        out = _pre_tool(data, session, call, scoped, fired, newcomers, all_rules, state)
        state.save()
    return out


def _pre_tool(data, session, call, scoped, fired, newcomers, all_rules, state):
    decision = guards.decide(scoped, call, session, state)
    if decision and decision.kind == "deny":
        attach = [decision.rule] if decision.rule else []
        send, rest = dl.bundle(attach, state)
        state.sent(send, rest)
        text = msg.decision_head("deny", decision.id, decision.reason) + dl.body_of(send)
        return hook_json("PreToolUse", text, msg.summary("PreToolUse", f"deny ({decision.id})"),
                         permissionDecision="deny", permissionDecisionReason=decision.reason)
    notes = "\n".join(ext.emit("PreToolUse", ext.Event("PreToolUse", data, session, call, state)))
    out = _pre_tool_rest(session, call, fired, newcomers, all_rules, state, decision, notes)
    if '"permissionDecision": "deny"' not in out:
        snapshot(data, call, session)
    if notes and not out:
        out = hook_json("PreToolUse", notes, msg.summary("PreToolUse", notes.splitlines()[0]))
    return out


def _pre_tool_rest(session, call, fired, newcomers, all_rules, state, decision, notes):
    prefix = notes + "\n" if notes else ""
    gated = [] if guards.no_deny() else [f for f in fired if f.gated and f.rule.rel not in state.gates
                                         and not state.fully_delivered(f.rule)]
    if gated:
        rules = [f.rule for f in gated]
        send, rest = dl.bundle(rules, state)
        state.gates |= {r.rel for r in rules}
        if send:
            state.sent(send, rest)
            reason = msg.gate_reason([r.rel for r in rules], [u.label for u in send],
                                     [u.label for u in rest])
            return hook_json("PreToolUse", prefix + msg.decision_head("deny", "gate", reason) + dl.body_of(send),
                             msg.summary("PreToolUse", "deny (gate) — " + ", ".join(r.rel for r in rules)),
                             permissionDecision="deny", permissionDecisionReason=reason)

    queued = _by_rel(all_rules, state.pending)
    extra = newcomers + queued
    if decision and decision.kind == "ask" and decision.id not in state.asks:
        state.asks.add(decision.id)
        rules = ([decision.rule] if decision.rule else []) + [f.rule for f in fired] + extra
        rules = list({r.rel: r for r in rules}.values())
        send, rest = dl.bundle(rules, state)
        revised = dl.revised(send, state)
        state.sent(send, rest)
        text = prefix + msg.decision_head("ask", decision.id, decision.reason)
        if send:
            text += "\n" + msg.rules_attached([u.label for u in send], revised) + dl.body_of(send)
        return hook_json("PreToolUse", text, msg.summary("PreToolUse", f"ask ({decision.id})"),
                         permissionDecision="ask", permissionDecisionReason=decision.reason)
    return _deliver(fired, extra, state, "PreToolUse", prefix=notes)


# ------------------------------------------------------------ git snapshots
def _snap_file(data):
    sid, tid = data.get("session_id"), data.get("tool_use_id")
    if not sid:
        return None
    return dl.state_dir() / (f"{sid}.{tid}.snap.json" if tid else f"{sid}.snap.json")


def _roots(call, session):
    """Checkouts a command works in (cwd, `cd`, `git -C`), writes to, or names by an
    existing absolute path. DESK_HOME only when the command reaches it: a parallel
    session editing the desk must not show up as this command's change."""
    roots = set()
    for p in [session.cwd, *call.dirs, *call.targets, *wh.existing_paths(call)]:
        r = homelib.checkout_root(p)
        if r:
            roots.add(str(r))
    return roots


def status_snapshot(roots):
    """{root: {path: mtime}} of changed and new files (`git status --porcelain -uall`)."""
    snap = {}
    for root in roots:
        try:
            out = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "-uall", "-z"],
                                 capture_output=True, text=True, timeout=5,
                                 env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"}).stdout
        except Exception:
            continue
        files = {}
        entries = out.split("\0")
        k = 0
        while k < len(entries):
            entry = entries[k]
            k += 1
            if len(entry) < 4:
                continue
            if entry[0] in "RC":          # rename/copy: the original name follows
                k += 1
            rel = entry[3:]
            try:
                files[rel] = (Path(root) / rel).stat().st_mtime
            except OSError:
                files[rel] = None
        snap[str(root)] = files
    return snap


def changed_files(before, after):
    out = []
    for root, files in after.items():
        prev = before.get(root, {})
        for rel, mt in files.items():
            if rel not in prev or prev[rel] != mt:
                out.append(str(Path(root) / rel))
    return sorted(out)


def snapshot(data, call, session):
    f = _snap_file(data)
    if call.tool != "Bash" or f is None:
        return
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(status_snapshot(_roots(call, session))), encoding="utf-8")
    except OSError:
        pass


def _load_snapshot(data):
    f = _snap_file(data)
    if f is None:
        return None
    try:
        snap = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        f.unlink()
    except OSError:
        pass
    return snap


# --------------------------------------------------------------- PostToolUse
def post_tool(data):
    session = homelib.session_from(data)
    call = wh.Call.from_hook(data)
    all_rules = _rules(session)
    with dl.session_lock(session.session_id):
        state = dl.State.load(session.session_id)
        mark_read(data, call, session, all_rules, state)
        out, changed = "", []
        if call.tool == "Bash":
            before = _load_snapshot(data)
            if before is not None:
                after = status_snapshot(set(before) | _roots(call, session))
                changed = changed_files(before, after)
                if changed:
                    out = report_changes(changed, call, session, all_rules, state)
        ext.load(session.home)
        notes = ext.emit("PostToolUse", ext.Event("PostToolUse", data, session, call, state, tuple(changed)))
        if notes:
            ctx = json.loads(out)["hookSpecificOutput"].get("additionalContext", "") if out else ""
            out = hook_json("PostToolUse", "\n".join(notes + ([ctx] if ctx else [])),
                            msg.summary("PostToolUse", notes[0].splitlines()[0]))
        state.save()
    return out


def mark_read(data, call, session, rules, state):
    """A rule file read in full is in context: the hook does not send it again."""
    by_path = {str(r.path): r for r in rules}
    paths = []
    if call.tool == "Read":
        fp = str(call.input.get("file_path") or "")
        if fp in by_path and not call.input.get("offset") and not call.input.get("limit"):
            paths = [fp]
    elif call.tool == "Bash":
        response = json.dumps(data.get("tool_response") or {}, ensure_ascii=False)
        paths = [p for p in wh.read_targets(call.command, call.cwd)
                 if p in by_path and fully_read(p, response)]
    for p in paths:
        state.sent(dl.units(by_path[p]))


def fully_read(path, response_text):
    """The output contains the file's last non-empty line: it was not cut to a preview."""
    try:
        lines = [l.strip() for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]
    except (OSError, UnicodeDecodeError):
        return False
    return bool(lines) and lines[-1] in (response_text or "")


def report_changes(changed, call, session, all_rules, state):
    """What the command really wrote: rules whose `paths:`/`desk:` match the changed
    files, and guard remarks (too late to deny — said so instead)."""
    notes, fired = [], []
    for p in changed:
        fake = wh.Call(tool="Write", input={"file_path": p, "content": guards.added_text(p)}, cwd=call.cwd)
        repos = wh.repos_touched(fake, session)
        scoped = [r for r in all_rules if homelib.in_scope(r, session, repos)]
        for d in guards.evaluate(scoped, fake, session):
            if d.kind == "ask" and d.id in state.asks:
                continue
            notes.append(msg.unconfirmed(d.reason))
            if d.rule:
                fired.append(wh.Fired(d.rule, [(None, "запрет правила")]))
        fired += wh.fired(deliverable(all_rules, session, repos), fake, session.home, session.context_name)
    if not notes and not fired:
        return ""
    notes = list(dict.fromkeys(notes))
    send, rest = dl.bundle(list({f.rule.rel: f.rule for f in fired}.values()), state)
    revised = dl.revised(send, state)
    state.sent(send, rest)
    text = "\n".join([msg.changed_files(changed), *notes])
    if send:
        text += "\n" + msg.rules_attached([u.label for u in send], revised) + dl.body_of(send)
    return hook_json("PostToolUse", text, msg.summary(
        "PostToolUse", f"изменено файлов: {len(changed)}", notes and "замечаний: " + str(len(notes))))


if __name__ == "__main__":
    sys.exit(main())
