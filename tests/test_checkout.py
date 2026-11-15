import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from desk import checkout, ext
from tests.helpers import HomeCase


def repo(tmp):
    root = Path(tmp) / "repo"
    root.mkdir()
    run = lambda *a: subprocess.run(["git", "-C", str(root), *a], capture_output=True)
    run("init", "-b", "develop")
    run("config", "user.email", "t@t")
    run("config", "user.name", "t")
    (root / "f.txt").write_text("x")
    run("add", "-A")
    run("commit", "-m", "init")
    run("update-ref", "refs/remotes/origin/develop", "HEAD")
    return root, run


class BranchesTest(HomeCase):
    files = {"contexts/acme/context.md": "---\nmatch: {default: true}\n---\n",
             "contexts/acme/desk.yml": "branches:\n  integration: develop\n  production: master\n"
                                       "  repos:\n    scrape: {integration: main, production: main}\n"
                                       "    half: {integration: dev}\n"}

    def test_repo_overrides_place_per_key(self):
        b = lambda r="": checkout.branches(self.home, "acme", r)
        self.assertEqual(b(), ("develop", "master"))
        self.assertEqual(b("scrape"), ("main", "main"))
        self.assertEqual(b("half"), ("dev", "master"))
        self.assertEqual(b("other"), ("develop", "master"))
        self.assertEqual(checkout.branches(self.home, "none"), ("", ""))


class StartTest(HomeCase):
    files = BranchesTest.files

    def test_start_brings_idle_checkout_to_base_and_fills_header(self):
        from desk import header as hdr, home
        root, run = repo(self.tmp)
        run("checkout", "-b", "wip/done")                 # merged into the base: idle
        s = home.session_from({"cwd": str(root), "session_id": "s1"}, self.home)
        h = hdr.Header("t")
        text = checkout.on_start(ext.Event("SessionStart", {"source": "startup"}, s, header=h))
        self.assertIn(checkout.AUTO_MARK, text)
        self.assertIn(checkout.BASE_MARK + " — на базе", text)
        self.assertEqual([r.label for r in h.rows], ["чекаут", "к базе"])
        self.assertIsNone(checkout.on_start(ext.Event("SessionStart", {}, home.session_from(
            {"cwd": str(self.home), "session_id": "s2"}, self.home), header=hdr.Header("t"))))   # desk itself


class StateTest(unittest.TestCase):
    def test_state_on_base_and_off_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, run = repo(tmp)
            st = checkout.state(root, "develop")
            self.assertTrue(st.on_base)
            self.assertEqual((st.behind, st.ahead, st.dirty), (0, 0, 0))
            run("checkout", "-b", "feature/x")
            (root / "g.txt").write_text("y")
            run("add", "-A")
            run("commit", "-m", "work")
            (root / "h.txt").write_text("z")
            st = checkout.state(root, "develop")
            self.assertEqual((st.on_base, st.behind, st.ahead, st.dirty), (False, 0, 1, 1))
            text = "\n".join(checkout.lines(st))
            self.assertIn("не интеграционная develop", text)
            self.assertIn("git fetch origin develop", text)
            self.assertNotIn(checkout.IDLE_MARK, text)          # dirty: the work may live here
            (root / "h.txt").unlink()
            run("update-ref", "refs/remotes/origin/feature/x", "HEAD")
            run("branch", "--set-upstream-to", "origin/feature/x")
            self.assertIn(checkout.IDLE_MARK, "\n".join(checkout.lines(checkout.state(root, "develop"))))

    def test_none_without_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _ = repo(tmp)
            self.assertIsNone(checkout.state(root, "dev"))
            self.assertIsNone(checkout.state(root, ""))
            self.assertEqual(checkout.lines(None), [])

    def test_bring_to_base_only_when_work_lives_elsewhere(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, run = repo(tmp)
            st = lambda: checkout.state(root, "develop")
            self.assertIsNone(checkout.bring_to_base(root, st()))        # on base
            run("checkout", "-b", "feature/x")
            (root / "g.txt").write_text("y")
            run("add", "-A")
            run("commit", "-m", "work")
            self.assertIsNone(checkout.bring_to_base(root, st()))        # own commit, not pushed
            run("remote", "add", "origin", str(root))
            run("update-ref", "refs/remotes/origin/feature/x", "HEAD")
            run("branch", "--set-upstream-to", "origin/feature/x")
            (root / "h.txt").write_text("z")
            self.assertIsNone(checkout.bring_to_base(root, st()))        # dirty
            (root / "h.txt").unlink()
            self.assertIn(checkout.AUTO_MARK, checkout.bring_to_base(root, st()))
            self.assertTrue(st().on_base)
            self.assertIn("feature/x", run("branch", "--list").stdout.decode())

    def test_bring_to_base_takes_merged_branch_without_upstream(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, run = repo(tmp)
            run("checkout", "-b", "wip/done")
            self.assertIn(checkout.AUTO_MARK, checkout.bring_to_base(root, checkout.state(root, "develop")))


class BusyTest(unittest.TestCase):
    def env(self, tmp, peer_cwd, age_min=5, alive=True):
        sessions, logs = Path(tmp) / "sessions", Path(tmp) / "projects" / "slug"
        sessions.mkdir()
        logs.mkdir(parents=True)
        (sessions / "111.json").write_text(json.dumps({"sessionId": "own", "cwd": peer_cwd}))
        (sessions / "222.json").write_text(json.dumps({"sessionId": "peer-session", "cwd": peer_cwd}))
        log = logs / "peer-session.jsonl"
        log.write_text("{}")
        os.utime(log, (time.time() - age_min * 60,) * 2)
        return dict(sessions_dir=sessions, transcripts_dir=Path(tmp) / "projects", alive=lambda pid: alive)

    def nested(self, tmp):
        root = Path(tmp) / "repo"
        (root / "app").mkdir(parents=True)
        run = lambda *a: subprocess.run(["git", "-C", str(root), *a], check=True, capture_output=True)
        run("init", "-q")
        run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init")
        tree = root / ".claude" / "worktrees" / "t-1"
        run("worktree", "add", "-q", "-b", "t-1", str(tree))
        return root, tree

    def test_peer_alive_recent_and_not_own(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, tree = self.nested(tmp)
            kw = self.env(tmp, str(root / "app"))
            self.assertEqual([p.sid for p in checkout.peer_sessions(str(root), "own", **kw)], ["peer-session"])
            self.assertTrue(checkout.busy(str(root), SimpleNamespace(session_id="own"), None, **kw))
            self.assertEqual(checkout.peer_sessions(str(tree), "own", **kw), [])   # a worktree is another copy
            kw["alive"] = lambda pid: False
            self.assertEqual(checkout.peer_sessions(str(root), "own", **kw), [])

    def test_idle_peer_and_nested_tree_do_not_hold(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, tree = self.nested(tmp)
            self.assertEqual(checkout.peer_sessions(str(root), "own", **self.env(tmp, str(tree))), [])
        with tempfile.TemporaryDirectory() as tmp:
            root, _ = self.nested(tmp)
            kw = self.env(tmp, str(root), age_min=checkout.PEER_IDLE_MIN + 5)
            self.assertEqual(checkout.peer_sessions(str(root), "own", **kw), [])

    def test_branch_in_work_by_resolver(self):
        st = SimpleNamespace(head="feature/t-2/x", integration="develop")
        resolver = ext.in_work(lambda names, context: names[0].startswith("feature/t-2"))
        try:
            with tempfile.TemporaryDirectory() as tmp:
                kw = dict(sessions_dir=Path(tmp), transcripts_dir=Path(tmp))
                self.assertEqual(checkout.busy(tmp, SimpleNamespace(session_id="own", context=None), st, **kw),
                                 ["ветка feature/t-2/x — задача в работе"])
        finally:
            ext.IN_WORK.remove(resolver)

    def test_busy_checkout_gets_no_switch_command(self):
        st = SimpleNamespace(head="bugfix/x", integration="develop", base="origin/develop", tree=False,
                             behind=0, ahead=0, dirty=1, unpushed=0, base_age_h=1, on_base=False)
        text = "\n".join(checkout.lines(st, ["ветка bugfix/x — задача в работе"]))
        self.assertIn(checkout.BUSY_MARK, text)
        self.assertNotIn("git checkout", text)
        self.assertIn("git checkout develop", "\n".join(checkout.lines(st)))


if __name__ == "__main__":
    unittest.main()
