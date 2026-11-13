"""Example extension: a check data cannot express cleanly.

`git push --force` to a shared branch is denied; `--force-with-lease` to a feature
branch passes. Delete this file if you do not want it.
"""
import re

from desk import ext

SHARED = ("main", "master", "develop")
_PUSH_RE = re.compile(r"\bgit\s+(?:-C\s+\S+\s+)?push\b([^;&|\n]*)")


@ext.live("no-force-push", "no force push to a shared branch")
def no_force_push(call, session):
    for args in _PUSH_RE.findall(call.command):
        forced = re.search(r"(?<![\w-])(--force|-f)(?![\w-])", args)
        if forced and any(re.search(rf"(?<![\w/-]){b}\b", args) for b in SHARED):
            return ("deny", "desk: force push to a shared branch — push a fix on top instead")
    return None


@ext.compliance("no-force-push", "no force push to a shared branch")
def no_force_push_cases(record, home):
    cases = []
    for ev in record["events"]:
        if ev["kind"] == "tool_use" and ev["name"] == "Bash":
            cmd = (ev.get("input") or {}).get("command", "")
            if _PUSH_RE.search(cmd):
                cases.append((no_force_push(ext.Call(tool="Bash", input={"command": cmd}), None) is None,
                               cmd[:160]))
    return cases
