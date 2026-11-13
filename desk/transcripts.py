"""Claude Code transcripts (`~/.claude/projects/<slug>/<session>.jsonl`) as events.

Event kinds: `tool_use` (name, input, id, ts, persisted — the result went to a
persisted file, error), `attachment` (hook output text with the `[desk]` mark,
hook event), `user_text`, `assistant_text`, `compact` (context reset).
"""
import json
import re
from pathlib import Path

from .messages import MARK

PROJECTS = Path.home() / ".claude" / "projects"


def records(path):
    """JSON records line by line; a broken or unfinished line is skipped."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    yield rec
    except OSError:
        return


def hook_text(att):
    """Text of a hook delivery as the model saw it. `content` is a string, a list of
    strings or a dict; `stdout` of hook_success is the raw hook JSON — unpacked, or
    multi-line marks would not be found."""
    content = att.get("content")
    if isinstance(content, list):
        content = "\n".join(str(x) for x in content)
    elif isinstance(content, dict):
        content = json.dumps(content, ensure_ascii=False)
    if content:
        return str(content)
    stdout = att.get("stdout") or ""
    if isinstance(stdout, str) and stdout.lstrip().startswith("{"):
        try:
            ctx = (json.loads(stdout).get("hookSpecificOutput") or {}).get("additionalContext")
            if isinstance(ctx, str):
                return ctx
        except (ValueError, AttributeError):
            pass
    return str(stdout)


def iter_session(path, mark=MARK):
    """{path, cwd, started, events} of one transcript."""
    events, cwd, started, seen = [], None, None, set()
    for rec in records(path):
        cwd = cwd or rec.get("cwd")
        started = started or rec.get("timestamp")
        if rec.get("type") == "system" and rec.get("subtype") == "compact_boundary":
            events.append({"kind": "compact"})
            continue
        if rec.get("type") == "attachment":
            att = rec.get("attachment") or {}
            if att.get("type") == "queued_command":
                if att.get("humanTurn") and isinstance(att.get("prompt"), str):
                    events.append({"kind": "user_text", "text": att["prompt"]})
                continue
            text = hook_text(att)
            # one delivery is stored twice: hook_success (stdout JSON) and
            # hook_additional_context (the same text as a list)
            key = (att.get("toolUseID"), att.get("hookEvent"), text[:400])
            if mark in text and key not in seen:
                seen.add(key)
                events.append({"kind": "attachment", "text": text, "event": att.get("hookEvent", "")})
            continue
        content = (rec.get("message") or {}).get("content")
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        if not isinstance(content, list):
            continue
        for c in content:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "text" and rec.get("type") in ("user", "assistant"):
                text = c.get("text") or ""
                # harness inserts (system-reminder, notifications) are not prompts
                if rec.get("isMeta") or (rec["type"] == "user" and re.match(r"\s*<(?!!--)", text)):
                    continue
                events.append({"kind": rec["type"] + "_text", "text": text})
            elif c.get("type") == "tool_use":
                events.append({"kind": "tool_use", "name": c.get("name") or "",
                               "input": c.get("input") if isinstance(c.get("input"), dict) else {},
                               "id": c.get("id"), "ts": rec.get("timestamp"),
                               "persisted": False, "error": False})
            elif c.get("type") == "tool_result":
                body = c.get("content") or ""
                if isinstance(body, list):
                    body = "\n".join(x.get("text", "") for x in body if isinstance(x, dict))
                for ev in reversed(events):
                    if ev["kind"] == "tool_use" and ev.get("id") == c.get("tool_use_id"):
                        ev["persisted"] = "<persisted-output>" in str(body)
                        ev["error"] = bool(c.get("is_error"))
                        break
    return {"path": str(path), "cwd": cwd, "started": started, "events": events}


def transcripts(root=None, since=None, session=None):
    """Transcript files; `since` — epoch seconds (older files are skipped unread),
    `session` — id, its prefix or a path."""
    root = Path(root or PROJECTS)
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.jsonl")):
            if session and not (f.stem.startswith(session) or str(f) == str(session)):
                continue
            if since is not None:
                try:
                    if f.stat().st_mtime < since:
                        continue
                except OSError:
                    continue
            yield f
