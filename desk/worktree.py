"""WorktreeCreate: the session's worktree from the place's integration branch, not
from `origin/HEAD`.

With this hook the harness (`--worktree`, EnterWorktree, desktop sessions in a
worktree) does not create the tree itself: the hook gets a slug (`name`) and `cwd`,
creates the tree and prints its absolute path — the only line of stdout.

* Folder — `<main checkout>/.claude/worktrees/<slug>`, also when started from
  another worktree.
* Base — a fresh `origin/<integration>` (`branches:` of the place, desk.checkout);
  none declared — `origin/HEAD`, then HEAD.
* Branch — `worktree.branch` of desk.yml with `{name}` (default: the slug), without
  an upstream: `@{u}..HEAD` and `git push` must not aim at the integration branch.
* A tree with this slug exists (a resumed session) — its path, nothing recreated.
* `.worktreeinclude` is not processed by the harness under a hook — done here.

Any failure of this logic — a plain `git worktree add` from HEAD: the session must
start even when the desk is broken. Log — `$TMPDIR/desk-hook/worktree.log`.
"""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

FETCH_TIMEOUT = 20
LOG = Path(os.environ.get("TMPDIR", "/tmp")) / "desk-hook" / "worktree.log"


def log(msg):
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%F %T')} {msg}\n")
    except OSError:
        pass


def git(cwd, *args, timeout=30):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=timeout)
    if r.stderr:
        sys.stderr.write(r.stderr)
    return r


def main_checkout(cwd):
    """The main checkout's root: from a worktree `--git-common-dir` leads to its `.git`."""
    r = git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    common = Path(r.stdout.strip())
    if r.returncode != 0 or not common.name:
        raise RuntimeError(f"не git-репозиторий: {cwd}")
    return common.parent if common.name == ".git" else Path(git(cwd, "rev-parse", "--show-toplevel").stdout.strip())


def is_tree(path):
    return path.is_dir() and git(path, "rev-parse", "--git-dir").returncode == 0


def settings(root, home=None):
    """(integration branch, branch pattern) for a checkout; the desk unreadable — ("", "{name}")."""
    try:
        from . import checkout, home as homelib
        s = homelib.session_from({"cwd": str(root)}, home, project_dir="")
        integration = checkout.branches(s.home, s.context_name, s.repo)[0] if s.context else ""
        pattern = str((homelib.config(s.home).get("worktree") or {}).get("branch") or "{name}")
        return integration, pattern
    except Exception as e:     # the tree is needed anyway
        log(f"desk: {e!r}")
        return "", "{name}"


def base_ref(root, integration):
    """origin/<integration> after a fetch; else origin/HEAD, then HEAD."""
    if integration:
        try:
            git(root, "fetch", "origin", integration, timeout=FETCH_TIMEOUT)
        except subprocess.TimeoutExpired:
            log(f"fetch origin {integration}: timeout, the cache is used")
        if git(root, "rev-parse", "--verify", "--quiet", f"origin/{integration}").returncode == 0:
            return f"origin/{integration}"
    if git(root, "rev-parse", "--verify", "--quiet", "origin/HEAD").returncode == 0:
        return "origin/HEAD"
    return "HEAD"


def copy_included(root, dest):
    """Ignored files matching `.worktreeinclude`, as the harness does."""
    if not (root / ".worktreeinclude").is_file():
        return
    r = git(root, "ls-files", "--others", "--ignored", "-z", "--exclude-from=.worktreeinclude")
    for rel in filter(None, r.stdout.split("\0")):
        src, dst = root / rel, dest / rel
        if src.is_file() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def create(name, cwd, home=None):
    root = main_checkout(cwd)
    dest = root / ".claude" / "worktrees" / name
    if is_tree(dest):
        log(f"reuse {dest}")
        return dest
    integration, pattern = settings(root, home)
    branch = pattern.replace("{name}", name)
    if git(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}").returncode == 0:
        r, base = git(root, "worktree", "add", str(dest), branch), branch
    else:
        base = base_ref(root, integration)
        r = git(root, "worktree", "add", "--no-track", "-b", branch, str(dest), base)
    if r.returncode != 0:
        raise RuntimeError(f"git worktree add: {r.stderr.strip()}")
    copy_included(root, dest)
    log(f"create {dest} branch={branch} base={base}")
    return dest


def fallback(name, cwd):
    """What the harness would do, in the same folder."""
    root = Path(git(cwd, "rev-parse", "--show-toplevel").stdout.strip() or cwd)
    dest = root / ".claude" / "worktrees" / name
    if is_tree(dest):
        return dest
    r = git(root, "worktree", "add", "-b", name, str(dest), "HEAD")
    if r.returncode != 0:
        raise RuntimeError(f"fallback git worktree add: {r.stderr.strip()}")
    log(f"fallback {dest}")
    return dest


def keeps_work(tree, bases):
    """Why the tree may not go, or "" — the work is somewhere else too: nothing
    uncommitted and the branch is in a base (local or origin) or fully pushed."""
    if git(tree, "status", "--porcelain", "-uall").stdout.strip():
        return "незакоммиченное"
    head = git(tree, "rev-parse", "HEAD").stdout.strip()
    for ref in bases:
        if git(tree, "rev-parse", "--verify", "--quiet", ref).returncode == 0 \
                and git(tree, "merge-base", "--is-ancestor", head, ref).returncode == 0:
            return ""
    if git(tree, "rev-parse", "--verify", "--quiet", "@{u}").returncode == 0 \
            and git(tree, "rev-list", "--count", "@{u}..HEAD").stdout.strip() == "0":
        return ""
    return "ветка не влита и не запушена"


def remove(path, home=None):
    """WorktreeRemove: the tree and its branch go only when the work lives elsewhere.
    Claude Code never deletes the branch of a hook-made tree — done here (`-d`: a branch
    git thinks unmerged stays). Kept — exit 1, the harness keeps the directory."""
    tree = Path(path)
    if not is_tree(tree):
        log(f"remove {tree}: not a tree")
        return 0
    root = main_checkout(tree)
    integration, _ = settings(root, home)
    main_branch = git(root, "symbolic-ref", "--quiet", "--short", "HEAD").stdout.strip()
    bases = [r for b in dict.fromkeys(filter(None, (integration, main_branch)))
             for r in (f"refs/heads/{b}", f"refs/remotes/origin/{b}")]
    why = keeps_work(tree, bases)
    if why:
        log(f"keep {tree}: {why}")
        sys.stderr.write(f"desk WorktreeRemove: дерево оставлено — {why}: {tree}\n")
        return 1
    branch = git(tree, "symbolic-ref", "--quiet", "--short", "HEAD").stdout.strip()
    r = git(root, "worktree", "remove", str(tree))
    if r.returncode != 0:
        log(f"remove {tree} failed: {r.stderr.strip()}")
        return 1
    if branch and branch != main_branch:
        git(root, "branch", "-d", branch)
    log(f"remove {tree} branch={branch}")
    return 0


def run_remove(data, home=None):
    try:
        return remove(data.get("worktree_path") or "", home)
    except Exception as e:      # unsure — keep the work
        log(f"remove failed: {e!r}")
        sys.stderr.write(f"desk WorktreeRemove: {e}\n")
        return 1


def run(data, home=None):
    """The hook: prints the tree's path; exit code 1 — no tree."""
    name, cwd = data.get("name") or "", data.get("cwd") or os.getcwd()
    log(f"input name={name!r} cwd={cwd!r}")
    if not name or "/" in name or name.startswith("."):
        sys.stderr.write(f"WorktreeCreate: недопустимый слаг {name!r}\n")
        return 1
    try:
        dest = create(name, cwd, home)
    except Exception as e:
        log(f"create failed: {e!r}")
        sys.stderr.write(f"desk WorktreeCreate: {e}; обычный git worktree add\n")
        try:
            dest = fallback(name, cwd)
        except Exception as e2:
            log(f"fallback failed: {e2!r}")
            sys.stderr.write(f"desk WorktreeCreate: {e2}\n")
            return 1
    print(dest.resolve())
    return 0
