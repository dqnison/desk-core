import datetime
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from desk import header as hdr, journal
from tests.helpers import HomeCase


class ShareTest(unittest.TestCase):
    def test_counts_home_rows_in_window_across_months(self):
        with tempfile.TemporaryDirectory() as tmp:
            j = Path(tmp)
            (j / "2026-10.md").write_text("- 2026-10-30 10:00 · desk · a <!-- 1 -->\n"
                                          "- 2026-10-01 10:00 · desk · old <!-- 2 -->\n")
            (j / "2026-11.md").write_text("# head\n- 2026-11-02 10:00 · shop-api · b <!-- 3 -->\n"
                                          "- 2026-11-03 11:00 · desk · c <!-- 4 -->\n")
            (j / "misses.md").write_text("- 2026-11-03 11:00 · desk · not a session <!-- 5 -->\n")
            self.assertEqual(journal.share(j, "desk", 7, datetime.date(2026, 11, 5)), (2, 3))


class ProjectTest(unittest.TestCase):
    def test_tree_session_goes_under_its_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "some-repo"
            repo.mkdir()
            git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
            git("init", "-q")
            git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init")
            tree = repo / ".claude" / "worktrees" / "brave-slug"
            git("worktree", "add", "-q", "--detach", str(tree))
            self.assertEqual(journal.project_of(str(tree)), "some-repo")
            self.assertEqual(journal.project_of(str(repo)), "some-repo")


class WriteTest(HomeCase):
    files = {"desk.yml": "journal: journal\nmeta_goal: 25\n"}

    def end(self, sid):
        path = self.tmp / f"{sid}.jsonl"
        path.write_text('{"type":"user","message":{"role":"user","content":"x"}}\n'
                        f'{{"type":"ai-title","aiTitle":"сессия {sid}"}}\n', encoding="utf-8")
        journal.on_end(SimpleNamespace(session=SimpleNamespace(home=self.home),
                                       data={"session_id": sid, "transcript_path": str(path), "cwd": str(self.home)}))

    def lines(self):
        month = self.home / "journal" / f"{datetime.datetime.now():%Y-%m}.md"
        return [l for l in month.read_text(encoding="utf-8").splitlines() if l.startswith("- ")]

    def test_parallel_sessions_keep_every_line_and_repeat_replaces(self):
        sids = [f"{i}{i}{i}{i}beef" for i in range(6)]
        ts = [threading.Thread(target=self.end, args=(sid,)) for sid in sids]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.end(sids[0])
        body = self.lines()
        self.assertEqual(len(body), len(sids))
        self.assertTrue(all(any(f"сессия {sid} <!-- {sid} -->" in l for l in body) for sid in sids))

    def test_start_shows_tail_and_meta_signal(self):
        self.end("cafe0001")                        # a session inside DESK_HOME: 1 of 1 > 25 %
        h = hdr.new([], SimpleNamespace(home=self.home, context=None, context_name=None, repo=None))
        text = journal.on_start(SimpleNamespace(session=SimpleNamespace(home=self.home), header=h))
        self.assertIn("Хвост журнала сессий (journal/)", text)
        self.assertIn("cafe0001", text)
        self.assertEqual([s.label for s in h.signals], ["мета"])


class OffTest(HomeCase):
    def test_no_journal_key_no_journal(self):
        ev = SimpleNamespace(session=SimpleNamespace(home=self.home), header=None,
                             data={"session_id": "x", "transcript_path": "/none"})
        self.assertIsNone(journal.on_start(ev))
        self.assertIsNone(journal.on_end(ev))
        self.assertFalse((self.home / "journal").exists())


if __name__ == "__main__":
    unittest.main()
