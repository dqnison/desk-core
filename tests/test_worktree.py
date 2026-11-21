import contextlib
import io
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from desk import checkout, worktree
from tests.helpers import HomeCase


def sh(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


def commit(cwd, msg):
    sh(cwd, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", msg)


class Repo(HomeCase):
    """A remote with production `master` and integration `develop`; a clone is the checkout."""
    files = {"desk.yml": "worktree: {branch: 'wip/{name}'}\n",
             "contexts/acme/context.md": "---\nmatch: {default: true}\n---\n",
             "contexts/acme/desk.yml": "branches: {integration: develop, production: master}\n"}

    def setUp(self):
        super().setUp()
        top = self.tmp.resolve()
        seed = top / "seed"
        sh(top, "init", "-q", "-b", "master", str(seed))
        commit(seed, "prod")
        sh(seed, "checkout", "-q", "-b", "develop")
        commit(seed, "integration")
        sh(seed, "checkout", "-q", "master")
        sh(top, "clone", "-q", "--bare", str(seed), str(top / "origin.git"))
        self.repo = top / "repo"
        sh(top, "clone", "-q", str(top / "origin.git"), str(self.repo))
        self.log = mock.patch.object(worktree, "LOG", top / "worktree.log")
        self.log.start()

    def tearDown(self):
        self.log.stop()
        super().tearDown()

    def hook(self, name, cwd):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = worktree.run({"name": name, "cwd": str(cwd)}, self.home)
        return code, out.getvalue()


class CreateTest(Repo):
    def test_tree_from_integration_with_pattern_branch_without_upstream(self):
        code, out = self.hook("brave-fox", self.repo)
        dest = self.repo / ".claude" / "worktrees" / "brave-fox"
        self.assertEqual((code, out), (0, f"{dest}\n"))
        self.assertEqual(sh(dest, "rev-parse", "--abbrev-ref", "HEAD"), "wip/brave-fox")
        self.assertEqual(sh(dest, "rev-parse", "HEAD"), sh(self.repo, "rev-parse", "origin/develop"))
        r = subprocess.run(["git", "-C", str(dest), "rev-parse", "--abbrev-ref", "@{u}"], capture_output=True)
        self.assertNotEqual(r.returncode, 0)

    def test_from_another_tree_lands_in_main_checkout_and_resume_reuses(self):
        _, first = self.hook("one", self.repo)
        self.assertEqual(self.hook("two", first.strip())[1].strip(), str(self.repo / ".claude" / "worktrees" / "two"))
        self.assertEqual(self.hook("one", self.repo), (0, first))

    def test_no_integration_falls_back_to_origin_head(self):
        (self.home / "contexts/acme/desk.yml").write_text("")
        _, out = self.hook("plain", self.repo)
        self.assertEqual(sh(out.strip(), "rev-parse", "HEAD"), sh(self.repo, "rev-parse", "origin/master"))

    def test_desk_broken_still_creates_tree(self):
        with mock.patch.object(checkout, "branches", side_effect=RuntimeError("boom")):
            code, out = self.hook("broken", self.repo)
        self.assertEqual(code, 0)
        self.assertTrue(Path(out.strip()).is_dir())

    def test_worktreeinclude_copied_and_bad_slug_refused(self):
        (self.repo / ".gitignore").write_text(".env\n")
        (self.repo / ".worktreeinclude").write_text(".env\n")
        (self.repo / ".env").write_text("X=1\n")
        _, out = self.hook("env", self.repo)
        self.assertEqual((Path(out.strip()) / ".env").read_text(), "X=1\n")
        self.assertEqual(self.hook("../escape", self.repo), (1, ""))


class RemoveTest(Repo):
    def tree(self, name):
        _, out = self.hook(name, self.repo)
        return Path(out.strip())

    def remove(self, tree):
        with contextlib.redirect_stderr(io.StringIO()):
            return worktree.run_remove({"worktree_path": str(tree)}, self.home)

    def branches(self):
        return sh(self.repo, "branch", "--list", "wip/*")

    def test_merged_tree_goes_with_its_branch(self):
        t = self.tree("done")
        commit(t, "work")
        sh(self.repo, "checkout", "-q", "develop")
        sh(self.repo, "merge", "-q", "--ff-only", "wip/done")     # влита в локальную, не запушена
        self.assertEqual(self.remove(t), 0)
        self.assertFalse(t.exists())
        self.assertEqual(self.branches(), "")

    def test_untouched_tree_goes(self):
        t = self.tree("empty")
        self.assertEqual(self.remove(t), 0)
        self.assertFalse(t.exists())

    def test_dirty_or_unmerged_tree_stays(self):
        dirty, unmerged = self.tree("dirty"), self.tree("unmerged")
        (dirty / "x.txt").write_text("draft")
        commit(unmerged, "only here")
        self.assertEqual((self.remove(dirty), self.remove(unmerged)), (1, 1))
        self.assertTrue(dirty.is_dir() and unmerged.is_dir())
        self.assertIn("wip/unmerged", self.branches())

    def test_missing_path_is_fine(self):
        self.assertEqual(self.remove(self.repo / ".claude" / "worktrees" / "gone"), 0)


class HookEntryTest(Repo):
    def test_hook_routes_worktree_create_even_when_quiet(self):
        import json, os
        from desk import hook
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"DESK_HOOKS_OFF": "1"}), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()):
            code = hook.main([], io.StringIO(json.dumps(
                {"hook_event_name": "WorktreeCreate", "name": "via-hook", "cwd": str(self.repo)})))
        self.assertEqual((code, out.getvalue().strip()), (0, str(self.repo / ".claude" / "worktrees" / "via-hook")))


class TreeLinesTest(Repo):
    """In a worktree: not `checkout <base>` (the main checkout holds it) but the tree's branch."""

    def lines(self, tree):
        st = checkout.state(tree, "develop")
        self.assertTrue(st.tree)
        return "\n".join(checkout.lines(st, prefix="wip/"))

    def test_placeholder_on_base_asks_rename(self):
        _, out = self.hook("fresh", self.repo)
        text = self.lines(out.strip())
        self.assertIn(checkout.TREE_MARK, text)
        self.assertIn("git branch -m", text)
        self.assertNotIn("git checkout develop", text)

    def test_named_on_base_is_quiet(self):
        _, out = self.hook("named", self.repo)
        sh(out.strip(), "branch", "-m", "bugfix/t-1/fix")
        self.assertEqual(len(checkout.lines(checkout.state(out.strip(), "develop"), prefix="wip/")), 1)

    def test_tree_from_production_reset_or_renamed(self):
        tree = self.repo / ".claude" / "worktrees" / "app"
        sh(self.repo, "worktree", "add", "-q", "-b", "claude/app", str(tree), "origin/master")
        self.assertIn(f"git checkout -B {checkout.TASK_BRANCH} origin/develop", self.lines(tree))
        commit(tree, "work")
        self.assertIn("git branch -m", self.lines(tree))
        self.assertNotIn("checkout -B", self.lines(tree))
        self.assertFalse(checkout.state(self.repo, "develop").tree)


if __name__ == "__main__":
    unittest.main()
