import os

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


class ExtensionTest(HomeCase):
    files = {
        "ext/no_force.py": """
from desk import ext

@ext.live("no-force-push", "no force push")
def no_force(call, session):
    if "push --force" in call.command:
        return ("deny", "desk: no force push")
""",
        "ext/broken.py": "raise RuntimeError('boom')\n",
    }

    def tearDown(self):
        ext.LIVE.pop("no-force-push", None)
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
        self.write({"desk.md": "---\ntier: import\ndesk:\n  portable: ['process/**']\n---\n"})
        from desk import checks
        self.assertTrue(checks.portable(self.home / "process/rule.md", self.home))
        self.assertFalse(checks.portable(self.home / "desk.md", self.home))
