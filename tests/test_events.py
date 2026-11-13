from desk import ext, home, lint, when as wh
from tests.helpers import HomeCase, git_checkout
from tests.test_deliver import ctx_of, run_hook

EXT = '''
from desk import ext

@ext.on("SessionStart")
def header(ev):
    return f"[desk] header for {ev.session.context_name}"

@ext.on("PreToolUse")
def once(ev):
    if ev.call.tool == "Bash" and "hint-me" in ev.call.command and "hinted" not in ev.state.notes:
        ev.state.notes.add("hinted")
        return "[desk] hint once"

@ext.on("PostToolUse")
def after(ev):
    if ev.call.tool == "Bash" and "watch-me" in ev.call.command:
        return "[desk] watch note"

@ext.on("SessionEnd")
def end(ev):
    (ev.session.home / "ended.txt").write_text(ev.data.get("session_id", ""))

@ext.on("PreToolUse")
def broken(ev):
    if "boom" in ev.call.command:
        raise RuntimeError("boom")
'''


class NamedAndAlsoTest(HomeCase):
    files = {
        "triggers.yml": "mr:\n  - command: 'mr-cli'\n  - skill: [mr]\nlogs:\n  - prompt: 'логи'\n",
        "process/a.md": "---\ntier: action\nwhen: [mr]\n---\na body\n",
        "process/b.md": "---\ntier: action\nwhen:\n  - mr\n  - command: 'other'\n---\nb body\n",
        "process/sensitive.md": "---\ntier: action\n---\ngeneral sensitive\n",
        "process/c.md": "---\ntier: action\nwhen:\n  - command: 'pay'\nalso: [process/sensitive.md]\n---\nc body\n",
    }

    def test_named_triggers_expand(self):
        rules = home.load_rules(self.home)
        got = {f.rule.rel: f.why for f in wh.fired(rules, wh.Call(tool="Bash", input={"command": "mr-cli list"}),
                                                    self.home)}
        self.assertEqual(got, {"process/a.md": "mr: команда «mr-cli»", "process/b.md": "mr: команда «mr-cli»"})

    def test_also_brings_rule(self):
        rules = home.load_rules(self.home)
        got = [f.rule.rel for f in wh.fired(rules, wh.Call(tool="Bash", input={"command": "pay now"}), self.home)]
        self.assertEqual(got, ["process/c.md", "process/sensitive.md"])

    def test_lint(self):
        self.assertEqual(lint.run(self.home), [])          # sensitive.md has no when: but is brought
        self.write({"process/d.md": "---\ntier: action\nwhen: [nope]\nalso: [process/missing.md]\n---\n",
                    "triggers.yml": "mr:\n  - comand: x\n"})
        msgs = [m for _, _, _, m in lint.run(self.home)]
        self.assertTrue(any("no trigger `nope`" in m for m in msgs))
        self.assertTrue(any("missing file process/missing.md" in m for m in msgs))
        self.assertTrue(any(m.startswith("mr entry 1: unknown keys") for m in msgs))


class EventsTest(HomeCase):
    files = {
        "ext/events.py": EXT,
        "process/git.md": "---\ntier: action\nwhen:\n  - git: write\n---\ngit body\n",
        "contexts/personal/context.md": "---\ntier: session\nmatch: {default: true}\n---\nhub\n",
    }

    def tearDown(self):
        for fns in ext.EVENTS.values():
            fns[:] = [f for f in fns if not f.__module__.startswith("desk_ext_")]
        super().tearDown()

    def hook(self, event, **kw):
        return run_hook({"hook_event_name": event, "session_id": "s1", "cwd": str(self.tmp), **kw})

    def test_session_start_text(self):
        text = ctx_of(self.hook("SessionStart", source="startup"))
        self.assertIn("[desk] header for personal", text)
        self.assertLess(text.index("header"), text.index("hub"))

    def test_pre_tool_note_once_and_with_delivery(self):
        out = self.hook("PreToolUse", tool_name="Bash", tool_input={"command": "git commit -m x # hint-me"})
        self.assertIn("[desk] hint once", ctx_of(out))
        self.assertIn("git body", ctx_of(out))
        out = self.hook("PreToolUse", tool_name="Bash", tool_input={"command": "echo hint-me"})
        self.assertEqual(out, {})

    def test_post_tool_and_broken_handler(self):
        out = self.hook("PostToolUse", tool_name="Bash", tool_input={"command": "watch-me"}, tool_response={})
        self.assertIn("[desk] watch note", ctx_of(out))
        out = self.hook("PreToolUse", tool_name="Bash", tool_input={"command": "boom"})
        self.assertIn("broken (PreToolUse) упал", ctx_of(out))

    def test_session_end(self):
        self.hook("SessionEnd", reason="exit")
        self.assertEqual((self.home / "ended.txt").read_text(), "s1")
