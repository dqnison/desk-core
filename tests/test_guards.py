import os
from pathlib import Path
import sys

from desk import ext, guards, home, when as wh
from tests.helpers import HomeCase, git_checkout
from tests.test_deliver import ctx_of, decision_of, run_hook

GIT_RULE = r"""---
tier: action
when:
  - git: write
guard:
  - deny: 'Co-Authored-By|Generated with|🤖'
    in: commit-message
    why: в коммитах нет AI-атрибуции
  - deny-unless: '^(Merge |Revert |fixup! |squash! )|^(?=.{1,72}$)(feat|fix|chore|docs|test|refactor)\((no-ref|[A-Z][A-Z0-9]*-\d+)\)!?: [a-z0-9`].*[^.\s]$'
    in: commit-subject
    why: subject по формату <type>(<TICKET>|no-ref) <description>
---
git body
"""


def bash(cmd, cwd="/w"):
    return wh.Call(tool="Bash", input={"command": cmd}, cwd=cwd)


class SafetyPackTest(HomeCase):
    files = {"process/safety.md": (Path(__file__).resolve().parents[1] / "desk/template/process/safety.md").read_text()}

    def kinds(self, call):
        s = home.session_from({"cwd": "/w", "session_id": "s1"}, self.home)
        return [(d.kind, d.id) for d in guards.evaluate(home.load_rules(self.home), call, s)]

    def test_rm_rf_on_root_home_or_star_denied_specific_folder_passes(self):
        for cmd in ("rm -rf /", "rm -rf ~", "sudo rm -fr /*", "cd x && rm -rf $HOME", "rm -rf *"):
            self.assertEqual(self.kinds(bash(cmd)), [("deny", "no-rm-rf-root")], cmd)
        for cmd in ("rm -rf ./build", "rm -rf /tmp/x", "rm -rf node_modules", "echo 'rm -rf /' > notes.md"):
            self.assertEqual(self.kinds(bash(cmd)), [], cmd)

    def test_writing_secrets_asks(self):
        app = git_checkout(self.tmp / "app")
        w = lambda rel: wh.Call(tool="Write", input={"file_path": str(app / rel), "content": "X=1"})
        self.assertEqual(self.kinds(w(".env")), [("ask", "secrets-write")])
        self.assertEqual(self.kinds(w("config/.env.local")), [("ask", "secrets-write")])
        self.assertEqual(self.kinds(w("settings.py")), [])


class InputScopeTest(HomeCase):
    files = {"process/mr.md": """---
tier: link
guard:
  - deny: 'Generated with'
    in: input
    tool: 'mcp__*merge_request*'
    why: no attribution in a merge request
---
"""}

    def test_input_scope_only_for_named_tools(self):
        s = home.session_from({"cwd": "/", "session_id": "s1"}, self.home)
        rules = home.load_rules(self.home)
        mr = lambda tool, text: wh.Call(tool=tool, input={"description": text})
        got = guards.evaluate(rules, mr("mcp__forge__create_merge_request", "x\n\nGenerated with Bot"), s)
        self.assertEqual([(d.kind, d.id) for d in got], [("deny", "process/mr.md#1")])
        self.assertEqual(guards.evaluate(rules, mr("mcp__forge__create_merge_request", "plain"), s), [])
        self.assertEqual(guards.evaluate(rules, mr("mcp__forge__create_issue", "Generated with Bot"), s), [])
        self.assertFalse(guards.lint_guard({"deny": "x", "in": "input", "tool": "mcp__*", "why": "y"}))


class GuardDataTest(HomeCase):
    files = {
        "process/git.md": GIT_RULE,
        "contexts/acme/context.md": "---\nmatch: {remote: git.acme.example.com}\n---\n",
        "contexts/acme/projects/shop-api/sensitive.md": """---
tier: action
when:
  - paths: ['billing/']
guard:
  - ask: 'billing/'
    in: paths
    why: платный API — сначала правило
---
sensitive body
""",
    }

    def setUp(self):
        super().setUp()
        self.api = git_checkout(self.tmp / "src/shop-api", "git@git.acme.example.com:team/shop-api.git")
        self.session = home.session_from({"cwd": str(self.api), "session_id": "s1"}, self.home)
        self.rules = [r for r in home.load_rules(self.home) if home.in_scope(r, self.session)]

    def kinds(self, call):
        return [(d.kind, d.id) for d in guards.evaluate(self.rules, call, self.session)]

    def test_attribution(self):
        cmd = 'git commit -m "$(cat <<\'EOF\'\nfix(no-ref): drop footer\n\nCo-Authored-By: Bot <b@example.com>\nEOF\n)"'
        self.assertEqual(self.kinds(bash(cmd)), [("deny", "process/git.md#1")])
        # a mention in a non-commit command is not a commit
        self.assertEqual(self.kinds(bash("grep -r 'Co-Authored-By' .")), [])
        # nor is a script in a heredoc that talks about committing
        script = "python3 - <<'PY'\ncmd = 'cd x && " + "git commit -m x'\nprint(cmd)\nPY\n"
        self.assertEqual(self.kinds(bash(script)), [])
        self.assertEqual(self.kinds(bash("cat > t.sh <<EOF\n" + "git commit -m 'Bad.'\nEOF")), [])

    def test_subject_format(self):
        self.assertEqual(self.kinds(bash('git commit -m "fix(DEMO-123): handle empty cart"')), [])
        self.assertEqual(self.kinds(bash("git commit -m 'Fixed stuff.'")), [("deny", "process/git.md#2")])
        self.assertEqual(self.kinds(bash('git commit -m "fix(no-ref): ' + "x" * 80 + '"')),
                         [("deny", "process/git.md#2")])
        self.assertEqual(self.kinds(bash("git commit -m 'Merge branch main'")), [])
        self.assertEqual(self.kinds(bash("git commit -F msg.txt")), [])     # unreadable: no decision
        d = guards.evaluate(self.rules, bash("git commit -m 'Fixed stuff.'"), self.session)[0]
        self.assertIn("Fixed stuff.", d.reason)
        self.assertIn("process/git.md", d.reason)

    def test_commit_is_its_own_segment(self):
        # review 14.11: a commit segment was the whole tail of the command
        self.assertEqual(self.kinds(bash("git commit --amend --no-edit && python3 -m unittest")), [])
        self.assertEqual(self.kinds(bash("git commit -m 'fix(no-ref): add cart' && echo 'Generated with x'")), [])
        self.assertEqual(self.kinds(bash("git commit -m 'Bad.' && grep -F x f")), [("deny", "process/git.md#2")])

    def test_commit_flag_groups_and_prefixes(self):
        for cmd in ("git commit -am 'Fixed stuff.'", "git commit -qam 'Fixed stuff.'",
                    "HUSKY=0 git commit -m 'Fixed stuff.'", "GIT_X='a b' git commit -m 'Fixed stuff.'",
                    "if git commit -m 'Fixed stuff.'; then echo ok; fi", "(git commit -m 'Fixed stuff.')",
                    "cd /r && git -C /r -c x=y commit -m 'Fixed stuff.' | cat"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.kinds(bash(cmd)), [("deny", "process/git.md#2")])
        for cmd in ("git commit -C HEAD", "git commit --fixup HEAD", "git commit -F msg.txt",
                    "git commit -m \"$MSG\"", "git log -m"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.kinds(bash(cmd)), [])

    def test_shell_segments(self):
        seg = wh.shell_segments
        self.assertEqual(seg("a && b || c; d | e & f"), ["a", "b", "c", "d", "e", "f"])
        self.assertEqual(seg("echo 'a; b' \"c && d\""), ["echo 'a; b' \"c && d\""])
        self.assertEqual(seg("x=$(git log; echo y) && z"), ["x=$(git log; echo y)", "z"])
        self.assertEqual(seg("cat > f <<'EOF'\ngit commit -m x; y\nEOF\nls"),
                         ["cat > f <<'EOF'\ngit commit -m x; y\nEOF", "ls"])
        self.assertEqual(seg("(cd a && make) && b"), ["cd a", "make", "b"])

    def test_paths_ask_only_in_its_repo(self):
        edit = wh.Call(tool="Edit", input={"file_path": str(self.api / "billing/pay.py")})
        self.assertEqual(self.kinds(edit), [("ask", "contexts/acme/projects/shop-api/sensitive.md#1")])
        self.assertEqual(self.kinds(wh.Call(tool="Edit", input={"file_path": str(self.api / "web/x.py")})), [])

    def test_no_deny_switch(self):
        os.environ["DESK_NO_DENY"] = "1"
        try:
            self.assertEqual(self.kinds(bash("git commit -m 'Fixed stuff.'")), [])
        finally:
            del os.environ["DESK_NO_DENY"]

    def test_hook_deny_carries_rule_and_ask_once(self):
        pre = lambda **kw: run_hook({"hook_event_name": "PreToolUse", "session_id": "s1",
                                     "cwd": str(self.api), **kw})
        out = pre(tool_name="Bash", tool_input={"command": "git commit -m 'Fixed stuff.'"})
        self.assertEqual(decision_of(out), "deny")
        self.assertIn("git body", ctx_of(out))
        edit = {"tool_name": "Edit", "tool_input": {"file_path": str(self.api / "billing/pay.py")}}
        out = pre(**edit)
        self.assertEqual(decision_of(out), "ask")
        self.assertIn("sensitive body", ctx_of(out))
        self.assertEqual(pre(**edit), {})                      # asked once, rule already in context

    def test_deny_queues_the_tail_of_a_big_rule(self):
        self.write({"process/git.md": GIT_RULE + "".join(f"## S{i}\n" + "x" * 5000 + "\n" for i in range(3))})
        from desk import deliver as dl
        out = run_hook({"hook_event_name": "PreToolUse", "session_id": "s9", "cwd": str(self.api),
                        "tool_name": "Bash", "tool_input": {"command": "git commit -m 'Fixed stuff.'"}})
        self.assertEqual(decision_of(out), "deny")
        self.assertEqual(dl.State.load("s9").pending, ["process/git.md"])

    def test_post_tool_remarks_unconfirmed_change(self):
        base = {"session_id": "s2", "cwd": str(self.api), "tool_name": "Bash", "tool_use_id": "t1",
                "tool_input": {"command": "./gen.sh"}}
        run_hook({**base, "hook_event_name": "PreToolUse"})
        (self.api / "billing").mkdir()
        (self.api / "billing/pay.py").write_text("x = 1\n")
        out = run_hook({**base, "hook_event_name": "PostToolUse", "tool_response": {}})
        self.assertIn("без подтверждения", ctx_of(out))
        self.assertIn("sensitive body", ctx_of(out))

    def test_lint_guard(self):
        self.assertEqual(guards.lint_guard({"deny": "x", "why": "y"}), [])
        self.assertTrue(guards.lint_guard({"deny": "(", "why": "y"}))
        self.assertTrue(guards.lint_guard({"deny": "x", "ask": "y", "why": "z"}))
        self.assertTrue(guards.lint_guard({"deny": "x", "in": "body", "why": "z"}))
        self.assertTrue(guards.lint_guard({"deny": "x"}))
        self.assertTrue(guards.lint_guard({"deny": "x", "why": "y", "since": "2026-01-01"}))
        self.assertFalse(guards.lint_guard({"deny": "x", "why": "y", "since": "2026-01-01", "id": "x"}))


class ExtensionTest(HomeCase):
    files = {
        "ext/no_force.py": """
from desk import ext
from forcelib import NEEDLE

@ext.live("no-force-push", "no force push")
def no_force(call, session):
    if NEEDLE in call.command:
        return ("deny", "desk: no force push")
""",
        "ext/forcelib/__init__.py": 'NEEDLE = "push --force"\n',   # shared code, not an extension
        "ext/broken.py": "raise RuntimeError('boom')\n",
    }

    def tearDown(self):
        ext.LIVE.pop("no-force-push", None)
        sys.modules.pop("forcelib", None)
        super().tearDown()

    def test_extension_decides_and_broken_one_is_reported(self):
        s = home.session_from({"cwd": "/", "session_id": "s1"}, self.home)
        got = guards.evaluate([], bash("git push --force"), s)
        self.assertEqual([(d.kind, d.id) for d in got], [("deny", "no-force-push")])
        self.assertTrue(any("broken.py" in e and "boom" in e for e in guards.load_errors(self.home)))


class PlaceWordsTest(HomeCase):
    files = {
        "contexts/acme/context.md": "---\nmatch: {remote: example.com}\n---\n",
        "contexts/acme/place-words.txt": "acme-internal  # company codename\nRoadrunner\n",
        "process/rule.md": "---\ntier: link\n---\n# Rule\n",
    }

    def test_live_ask_on_portable_layer_only(self):
        s = home.session_from({"cwd": "/", "session_id": "s1"}, self.home)
        w = lambda rel, text: wh.Call(tool="Write", input={"file_path": str(self.home / rel), "content": text})
        got = guards.evaluate([], w("process/rule.md", "deploy to roadrunner"), s)
        self.assertEqual([(d.kind, d.id) for d in got], [("ask", "place-words")])
        self.assertIn("Roadrunner", got[0].reason)
        self.assertEqual(guards.evaluate([], w("contexts/acme/x.md", "roadrunner"), s), [])
        self.assertEqual(guards.evaluate([], w("process/rule.md", "`roadrunner` [x](roadrunner.md)"), s), [])
        self.assertEqual(guards.evaluate([], w("process/rule.md", "roadrunner <!-- lint:place-ok -->"), s), [])

    def test_portable_from_config(self):
        self.write({"desk.md": "---\ntier: import\n---\n", "desk.yml": "portable: ['process/**']\n"})
        from desk import checks
        self.assertTrue(checks.portable(self.home / "process/rule.md", self.home))
        self.assertFalse(checks.portable(self.home / "desk.md", self.home))
