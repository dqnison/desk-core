import os
import subprocess
import time
import unittest
from pathlib import Path

from desk import ext, home, trees, when as wh
from tests.helpers import HomeCase


def git_repo(path, name, host="git.work.example.com"):
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "remote", "add", "origin", f"git@{host}:core/{name}.git"], check=True)
    return path


class VerdictTest(unittest.TestCase):
    def test_order_holds_live_fresh_drop(self):
        v = trees.tree_verdict
        self.assertEqual(v(dirty=1, unpushed=0, has_upstream=True, merged=True, in_work=False), trees.TREE_HOLDS)
        self.assertEqual(v(0, 2, True, False, False), trees.TREE_HOLDS)          # unpushed
        self.assertEqual(v(0, 0, False, False, False), trees.TREE_HOLDS)         # the only copy
        self.assertEqual(v(0, 0, True, False, None), trees.TREE_LIVE)            # nobody knows — in work
        self.assertEqual(v(0, 0, True, False, False, fresh=True), trees.TREE_FRESH)
        self.assertEqual(v(0, 0, True, False, False), trees.TREE_DROP)
        self.assertEqual(v(0, 0, False, True, False), trees.TREE_DROP)           # merged, no upstream

    def test_ticket_key_is_a_whole_segment(self):
        self.assertEqual(trees.ticket_key("feature/demo-12/x"), "DEMO-12")
        self.assertEqual(trees.ticket_key("charming-shirley-5e9f19"), None)
        self.assertEqual(trees.ticket_key("", "demo-7-fix"), "DEMO-7")


class SweepTest(HomeCase):
    files = {"contexts/work/context.md": "---\nmatch: {remote: git.work.example.com}\n---\n",
             "contexts/work/desk.yml": "branches: {integration: main, production: main}\n"}

    def test_merged_pushed_tree_dropped_unless_in_work(self):
        root = self.tmp / "app"
        root.mkdir()
        run = lambda *a, cwd=root: subprocess.run(["git", "-C", str(cwd), *a], capture_output=True, check=True)
        old = f"@{int(time.time()) - 7 * 86400} +0000"
        env = {**os.environ, "GIT_COMMITTER_DATE": old, "GIT_AUTHOR_DATE": old}
        subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
        run("remote", "add", "origin", "git@git.work.example.com:core/app.git")
        subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                        "--allow-empty", "-m", "init"], check=True, env=env)
        run("update-ref", "refs/remotes/origin/main", "HEAD")
        for key in ("demo-1", "demo-2"):
            subprocess.run(["git", "-C", str(root), "worktree", "add", "-q", "-b", f"feature/{key}/x",
                            str(self.tmp / key), "main"], check=True, env=env)
        resolver = ext.in_work(lambda names, ctx: trees.ticket_key(*names) == "DEMO-2")
        try:
            got = {t.slug: t.verdict for t in trees.inventory(self.home, [root], procs=[])[root][2]}
            lines = trees.sweep_lines(self.home, root, [root])
        finally:
            ext.IN_WORK.remove(resolver)
        self.assertEqual(got, {"demo-1": trees.TREE_DROP, "demo-2": trees.TREE_LIVE})
        self.assertTrue(any(ln.startswith("Сносимо в app: demo-1") for ln in lines), lines)


class ForeignCheckoutTest(HomeCase):
    """A command works in a known checkout of the place while the session is in another
    repository: that repository's layer will not arrive until the session moves."""
    files = {"contexts/work/context.md": "---\nmatch: {remote: git.work.example.com}\n---\n",
             "contexts/mine/context.md": "---\nmatch: {default: true}\n---\n"}

    def setUp(self):
        super().setUp()
        top = self.tmp.resolve()
        self.api = git_repo(top / "shop-api", "shop-api")
        self.api_tree = git_repo(top / "shop-api-worktrees" / "t-1", "shop-api")
        self.web = git_repo(top / "shop-web", "shop-web")
        self.web_tree = git_repo(top / "shop-web-worktrees" / "t-2", "shop-web")
        self.pet = git_repo(top / "pet", "pet", host="git.home.example.org")   # another place
        self.known = [self.api, self.web, self.pet]

    def found(self, cmd, cwd=None):
        s = home.session_from({"cwd": str(cwd or self.api), "session_id": "s"}, self.home, project_dir="")
        return trees.foreign_checkouts(wh.Call(tool="Bash", input={"command": cmd}, cwd=str(cwd or self.api)),
                                       s, self.known)

    def test_git_c_cd_relative_variable_and_tree_path(self):
        self.assertEqual(self.found(f"git -C {self.web} log --oneline -5"), {"shop-web": self.web})
        self.assertEqual(self.found(f"cd {self.web} && git grep Foo"), {"shop-web": self.web})
        self.assertEqual(self.found("cd ../shop-web && ls"), {"shop-web": self.web})
        self.assertEqual(self.found(f'D={self.web}; git -C "$D" status'), {"shop-web": self.web})
        self.assertEqual(self.found(f"sed -n 1,40p {self.web_tree}/src/Foo.php"), {"shop-web": self.web_tree})

    def test_silent_cases(self):
        self.assertEqual(self.found(f"git -C {self.api} log; cat {self.api}/x.php"), {})        # own repo
        self.assertEqual(self.found(f"git -C {self.api} log", cwd=self.api_tree), {})
        self.assertEqual(self.found(f"cat {self.home}/contexts/work/projects/shop-web/a.md"), {})  # desk files
        self.assertEqual(self.found(f"git -C {self.web} log", cwd=self.home), {})                 # from the desk
        self.assertEqual(self.found(f"git -C {self.pet} log"), {})                                # another place
        mirror = git_repo(self.tmp.resolve() / "shop-web-mirror", "shop-web-mirror")
        self.assertEqual(self.found(f"ls {mirror}/src"), {})                                      # not known


if __name__ == "__main__":
    unittest.main()
