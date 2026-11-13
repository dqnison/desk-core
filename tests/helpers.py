"""Fixture DESK_HOME and git checkouts in a temp dir."""
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


def write(root, files):
    for rel, text in files.items():
        p = Path(root) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")


def git_checkout(path, remote=None):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    if remote:
        subprocess.run(["git", "-C", str(path), "remote", "add", "origin", remote], check=True)
    return path


class HomeCase(unittest.TestCase):
    """A temp dir with `home/` (DESK_HOME) and `state/` (hook state); env set."""

    files = {}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="desk-test-")).resolve()
        self.home = self.tmp / "home"
        self.home.mkdir()
        write(self.home, self.files)
        self._env = {k: os.environ.get(k) for k in ("DESK_HOME", "DESK_STATE_DIR", "DESK_HOOKS_OFF")}
        os.environ["DESK_HOME"] = str(self.home)
        os.environ["DESK_STATE_DIR"] = str(self.tmp / "state")
        os.environ.pop("DESK_HOOKS_OFF", None)
        from desk import home
        home.remotes_of.cache_clear()
        home.checkout_root.cache_clear()

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, files):
        write(self.home, files)
