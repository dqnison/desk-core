import unittest
from pathlib import Path

from desk import home, when as wh
from tests.helpers import HomeCase, git_checkout


def bash(cmd, cwd="/w"):
    return wh.Call(tool="Bash", input={"command": cmd}, cwd=cwd)


class ShellParsingTest(HomeCase):
    def test_write_targets(self):
        t = wh.write_targets
        self.assertEqual(t("echo hi > out.txt", "/w"), ["/w/out.txt"])
        self.assertEqual(t("cmd 2>/dev/null; cmd >&2; cmd 2>&1", "/w"), [])
        self.assertEqual(t("cd /src && sed -i 's/a/b/' app/x.py", "/w"), ["/src/app/x.py"])
        self.assertEqual(t("F=notes.md; echo x >> $F", "/w"), ["/w/notes.md"])
        self.assertEqual(t("cp a.txt b/c.txt && mv x y", "/w"), ["/w/b/c.txt", "/w/y"])
        self.assertEqual(t("rm -rf build tmp/x", "/w"), ["/w/build", "/w/tmp/x"])
        self.assertEqual(t("git checkout -- a.py b.py", "/w"), ["/w/a.py", "/w/b.py"])
        self.assertEqual(t("cat a.md | tee -a log.txt", "/w"), ["/w/log.txt"])
        self.assertEqual(t("echo x > sub/../f.md", "/w"), ["/w/f.md"])
        self.assertIn("/w/conf.json", t("python3 -c \"open('conf.json','w').write('{}')\"", "/w"))
        self.assertEqual(t("cat a.md; grep -r x .", "/w"), [])
        # a heredoc body is data: a quoted reply line is not a redirect
        self.assertEqual(t("cat > notes.md <<'EOF'\n> quoted reply\ncp a b\nEOF", "/w"), ["/w/notes.md"])
        # …but an interpreter script fed by a heredoc writes what it names
        self.assertIn("/w/out.json", t("python3 - <<'PY'\nopen('out.json','w').write('1')\nPY", "/w"))

    def test_unknown_user_home_does_not_raise(self):
        self.assertEqual(wh.write_targets("cd ~nouser && echo x > ~nouser2/f", "/w"), ["/w/~nouser/~nouser2/f"])
        self.assertEqual(bash("cd ~nouser && ls").dirs[:1], ["/w/~nouser"])

    def test_git_subcommands(self):
        self.assertEqual(wh.git_subcommands("git -C /x --no-pager log -5 && git commit -m x"), ["log", "commit"])
        self.assertEqual(wh.git_subcommands("cat git-flow.md"), [])

    def test_absolute_glob_keeps_trailing_slash(self):
        self.assertTrue(wh.paths_match(["/abs/dir/"], "/abs/dir/a/b.py", self.home))
        self.assertFalse(wh.paths_match(["/abs/dir/"], "/abs/dirx/b.py", self.home))

    def test_globs(self):
        g = wh.glob_match
        self.assertTrue(g("**/now.md", "now.md"))
        self.assertTrue(g("**/now.md", "contexts/acme/now.md"))
        self.assertTrue(g("billing/", "billing/a/b.py"))
        self.assertTrue(g("billing/*.py", "billing/a.py"))
        self.assertFalse(g("billing/*.py", "billing/a/b.py"))
        self.assertFalse(g("*.md", "a/b.md"))


class MatchTest(HomeCase):
    def m(self, entry, call, repo=None):
        return wh.match_entry(entry, call, self.home, repo)

    def test_git_write_and_list(self):
        self.assertTrue(self.m({"git": "write"}, bash("git commit -m x")))
        self.assertFalse(self.m({"git": "write"}, bash("git -C /x status && git log")))
        self.assertTrue(self.m({"git": ["worktree"]}, bash("git -C /x worktree list")))
        self.assertFalse(self.m({"git": ["worktree"]}, bash("git status; cat worktree.md")))

    def test_tool_command_input_skill(self):
        mcp = wh.Call(tool="mcp__containers__docker_proxy", input={"path": "/c/1/logs", "n": {"x": 1}})
        self.assertTrue(self.m({"tool": "mcp__containers__*", "input": {"path": "/logs"}}, mcp))
        self.assertFalse(self.m({"tool": "mcp__containers__*", "input": {"path": "/stats"}}, mcp))
        self.assertTrue(self.m({"input": {"n.x": "^1$"}}, mcp))
        self.assertTrue(self.m({"command": r"\bdocker\s+compose\b"}, bash("docker compose up")))
        self.assertFalse(self.m({"command": "docker"}, mcp))          # command implies Bash
        self.assertFalse(self.m({"command": "APP_DB_"}, bash("echo app_db_x")))   # commands: case matters
        self.assertTrue(self.m({"prompt": "логи"}, wh.Call(prompt="ЛОГИ")))         # prompts: it does not
        skill = wh.Call(tool="Skill", input={"skill": "acme-tools:db-inspect"})
        self.assertTrue(self.m({"skill": ["db-inspect"]}, skill))
        self.assertFalse(self.m({"skill": "other"}, skill))

    def test_all_keys_of_an_entry_must_match(self):
        self.assertFalse(self.m({"tool": "Bash", "git": "write"}, bash("ls")))
        self.assertTrue(self.m({"tool": "Bash", "git": "write"}, bash("git push")))

    def test_unknown_key_never_matches(self):
        self.assertIsNone(self.m({"tool": "Bash", "comand": "x"}, bash("x")))
        self.assertIsNone(self.m({"gate": True}, bash("x")))

    def test_prompt_only_for_prompts(self):
        p = wh.Call(prompt="покажи логи за час")
        self.assertTrue(self.m({"prompt": r"(?<![а-яё])лог(и|ов)?(?![а-яё])"}, p))
        self.assertFalse(self.m({"prompt": "логи"}, bash("echo логи")))
        self.assertFalse(self.m({"tool": "Bash"}, p))
        self.assertFalse(self.m({"prompt": "логи", "tool": "Bash"}, p))

    def test_paths_relative_to_checkout_and_home(self):
        self.write({"now.md": "x"})
        call = wh.Call(tool="Edit", input={"file_path": str(self.home / "contexts/acme/now.md")})
        self.assertTrue(self.m({"paths": "**/now.md"}, call))
        self.assertTrue(self.m({"paths": "contexts/"}, call))
        self.assertFalse(self.m({"paths": "process/"}, call))

    def test_repo_rule_paths_only_in_that_repo(self):
        api = git_checkout(self.tmp / "src/shop-api", "git@example.com:acme/shop-api.git")
        web = git_checkout(self.tmp / "src/web", "git@example.com:acme/web.git")
        edit = lambda p: wh.Call(tool="Write", input={"file_path": str(p)})
        self.assertTrue(self.m({"paths": "billing/**"}, edit(api / "billing/pay.py"), repo="shop-api"))
        self.assertFalse(self.m({"paths": "billing/**"}, edit(web / "billing/pay.py"), repo="shop-api"))
        # the same write through the shell
        self.assertTrue(self.m({"paths": "billing/"}, bash("sed -i s/a/b/ billing/pay.py", str(api)),
                               repo="shop-api"))

    def test_desk_predicate(self):
        h = self.home
        self.assertTrue(self.m({"desk": True}, wh.Call(tool="Write", input={"file_path": str(h / "a.md")})))
        self.assertTrue(self.m({"desk": True}, bash("ls", str(h / "process"))))
        self.assertTrue(self.m({"desk": True}, bash(f"git -C {h} commit -m x", "/src")))
        self.assertTrue(self.m({"desk": True}, bash(f"cd {h} && git add -A && git commit -m x", "/src")))
        self.assertFalse(self.m({"desk": True}, bash(f"cd {h} && grep -rn x process", "/src")))
        self.assertFalse(self.m({"desk": True}, bash(f"cd {h} && git diff --stat", "/src")))
        self.assertFalse(self.m({"desk": True}, bash(f"git -C {h} log -3", "/src")))
        self.assertTrue(self.m({"desk": True}, bash(f"echo x >> {h}/now.md")))
        self.assertFalse(self.m({"desk": True}, bash(f"cat {h}/process/git.md", "/src")))
        self.assertFalse(self.m({"desk": True}, bash("ls", "/src")))
        # a shell in the desk touches it with any command, even one that goes elsewhere
        self.assertTrue(self.m({"desk": True}, bash("cd /src && git status", str(h))))


class FiredTest(HomeCase):
    files = {
        "process/git.md": "---\ntier: action\nwhen:\n  - git: write\n  - prompt: commit\n---\n",
        "process/logs.md": "---\ntier: action\nwhen:\n  - tool: 'mcp__metrics*'\n    gate: true\n---\n",
        "contexts/acme/context.md": "---\nmatch: {remote: example.com}\n---\n",
    }

    def test_fired_and_gate(self):
        rules = home.load_rules(self.home)
        got = wh.fired(rules, bash("git push"), self.home)
        self.assertEqual([f.rule.rel for f in got], ["process/git.md"])
        self.assertEqual(got[0].why, "git push")
        self.assertFalse(got[0].gated)
        got = wh.fired(rules, wh.Call(tool="mcp__metrics__query"), self.home)
        self.assertTrue(got[0].gated)

    def test_repos_touched(self):
        self.write({"contexts/acme/projects/shop-api/local.md": "---\ntier: session\n---\n"})
        api = git_checkout(self.tmp / "src/shop-api", "git@example.com:acme/shop-api.git")
        s = home.session_from({"cwd": "/"}, self.home)
        s.context = home.load_contexts(self.home)[0]
        self.assertEqual(wh.repos_touched(bash(f"cd {api} && ls"), s), {"shop-api"})
        self.assertEqual(wh.repos_touched(bash("ls /src/shop-api-old"), s), set())
        # a worktree in a directory of another name is still its repo (by remote)
        tree = git_checkout(self.tmp / "trees" / "fix-cart", "git@example.com:acme/shop-api.git")
        self.assertEqual(wh.repos_touched(bash(f"W={tree}; head -3 $W/README.md"), s), {"shop-api"})
        # a repo named inside a heredoc body is data, not work in it
        self.assertEqual(wh.repos_touched(bash("cat > notes.md <<'EOF'\nran cd /src/shop-api\nEOF"), s), set())
        self.assertEqual(wh.repos_touched(bash("cd /src/shop-api && cat <<EOF\nx\nEOF"), s), {"shop-api"})
        # DESK_HOME in any spelling and URLs are not repository work
        import os
        s.home = self.home
        os.environ["HOME"], old_home = str(self.tmp), os.environ.get("HOME")
        try:
            self.assertEqual(wh.repos_touched(bash("cat ~/home/contexts/acme/projects/shop-api/x.md"), s), set())
            self.assertEqual(wh.repos_touched(bash("cat $HOME/home/contexts/acme/projects/shop-api/x.md"), s), set())
        finally:
            os.environ["HOME"] = old_home
        self.assertEqual(wh.repos_touched(bash("curl https://git.example.com/acme/shop-api/-/merge_requests/1"), s),
                         set())
        # desk files are not repository work
        self.assertEqual(wh.repos_touched(bash(f"cat {self.home}/contexts/acme/context.md"), s), set())
        self.assertEqual(wh.repos_touched(wh.Call(tool="Edit", input={"file_path": str(api / "x.py")}), s),
                         {"shop-api"})


class WriteTargetsTest(unittest.TestCase):
    """What a Bash command writes: redirects, tee, in-place edits, heredoc scripts."""

    def test_redirect_tee_sed(self):
        cmd = ("cat >> ~/desk/a.md <<'EOF'\nx\nEOF\necho y > /tmp/b.txt; echo z | tee /tmp/c.txt; "
               "sed -i '' 's/a/b/' /tmp/d.md")
        targets = wh.write_targets(cmd)
        self.assertIn(str(Path("~/desk/a.md").expanduser()), targets)
        for p in ("/tmp/b.txt", "/tmp/c.txt", "/tmp/d.md"):
            self.assertIn(p, targets)

    def test_python_heredoc_write_and_read(self):
        write = ('~/.venv/bin/python - <<\'PY\'\nimport io\np = "/Users/x/desk/contexts/a/db.md"\n'
                 's = io.open(p, encoding="utf-8").read()\nio.open(p, "w", encoding="utf-8").write(s)\nPY')
        self.assertIn("/Users/x/desk/contexts/a/db.md", wh.write_targets(write))
        read = 'python3 - <<\'PY\'\nprint(open("/Users/x/desk/a.md").read())\nPY'
        self.assertEqual(wh.write_targets(read), [])

    def test_relative_paths_after_cd_and_vars(self):
        cmd = "W=/Users/x/app-worktrees/t-1; cd $W && cat > tests/T.php <<'PHP'\n<?php\nPHP"
        self.assertIn("/Users/x/app-worktrees/t-1/tests/T.php", wh.write_targets(cmd, "/elsewhere"))
        cmd2 = "F=~/repo/.env.local\ncp -p \"$F\" \"$F.bak\"; perl -pi -e 's/a/b/' \"$F\""
        targets = wh.write_targets(cmd2, "/elsewhere")
        self.assertIn(str(Path("~/repo/.env.local").expanduser()), targets)
        self.assertIn(str(Path("~/repo/.env.local.bak").expanduser()), targets)

    def test_python_relative_open_uses_cwd(self):
        cmd = "cd /Users/x/app && python3 - <<'PY'\np='models/A.php'\ns=open(p).read()\nopen(p,'w').write(s)\nPY"
        self.assertIn("/Users/x/app/models/A.php", wh.write_targets(cmd, "/elsewhere"))

    def test_git_checkout_restores_files(self):
        targets = wh.write_targets("cd /Users/x/repo; git checkout HEAD~1 -- a.php b.php && ls", "/elsewhere")
        self.assertIn("/Users/x/repo/a.php", targets)
        self.assertIn("/Users/x/repo/b.php", targets)

    def test_not_writes(self):
        self.assertEqual(wh.write_targets("ls /x 2>/dev/null; cmd 2>&1 | head", "/tmp"), [])
        self.assertEqual(wh.write_targets("cat ~/desk/a.md | grep x"), [])

    def test_var_with_backslashes_and_dotfile(self):
        self.assertIn("/tmp/out.txt", wh.write_targets("P='C:\\\\x\\\\y'; echo $P > /tmp/out.txt", "/tmp"))
        self.assertIn("/Users/x/desk/.gitignore",
                      wh.write_targets("cd /Users/x/desk && sed -i '' '/^work/d' .gitignore", "/tmp"))

    def test_call_targets_per_tool(self):
        self.assertEqual(wh.Call(tool="Edit", input={"file_path": "/x/y.md"}).targets, ["/x/y.md"])
        self.assertEqual(wh.Call(tool="Bash", input={"command": "echo 1 > /x/y.md"}).targets, ["/x/y.md"])
        self.assertEqual(wh.Call(tool="Read", input={"file_path": "/x/y.md"}).targets, [])

    def test_named_paths_resolve_after_cd(self):
        call = wh.Call(tool="Bash", input={"command": "W=/r/app; cd $W && cat src/a.py ../b/c.md"}, cwd="/x")
        self.assertIn("/r/app/src/a.py", call.named_paths)
        self.assertIn("/r/b/c.md", call.named_paths)
        self.assertEqual(wh.Call(tool="Read", input={"file_path": "/r/a"}).named_paths, [])

