import unittest

from desk import header as hdr
from tests.helpers import HomeCase
from tests.test_deliver import ctx_of, run_hook


class RenderTest(unittest.TestCase):
    def test_frame_wraps_long_value_and_marks_signal(self):
        h = hdr.Header("x")
        h.row("подача", "y", "📦")
        h.signal("занято", "сессия " * 20, "🚧")
        box = hdr.frame(h)[1:-1]
        self.assertTrue(box[1].startswith("│ 📦 подача"))
        self.assertTrue(box[2].startswith("├─ сигналы "))      # the separator — only with signals
        self.assertTrue(box[3].startswith("│ 🚧 занято"))
        self.assertGreater(len(box), 6)                     # the long value wrapped
        self.assertEqual(box[4].index("сессия"), box[3].index("сессия") + 1)   # emoji — two cells
        self.assertTrue(all(len(s) <= hdr.FRAME_W + 1 for s in box))
        h.signals.clear()
        self.assertFalse(any("сигналы" in s for s in hdr.frame(h)))

    def test_long_title_falls_back_to_a_row(self):
        h = hdr.Header("acme → " + "x" * 70)
        box = hdr.frame(h)[1:-1]
        self.assertTrue(len(box[0]) <= hdr.FRAME_W + 1 and "acme" not in box[0])
        self.assertTrue(box[1].startswith("│ 🧭 контекст  acme"))

    def test_widget_marks_signals_with_warning_tint(self):
        h = hdr.Header("personal")
        h.row("подача", "a · b", icon="package")
        h.signal("уборка", "сносимо в r: x", icon="trash")
        code = hdr.widget(h)
        self.assertEqual(code.count("var(--bg-warning)"), 1)
        self.assertEqual(code.count("var(--surface-1)"), 2)  # a chip per « · » part
        self.assertIn("ti-trash", code)
        self.assertNotIn("<script", code)

    def test_plural(self):
        f = lambda n: hdr.plural(n, "триггером", "триггерами", "триггерами")
        self.assertEqual([f(1), f(2), f(11), f(21)], ["триггером", "триггерами", "триггерами", "триггером"])


class HookHeaderTest(HomeCase):
    files = {
        "desk.yml": "header: true\n",
        "desk.md": "---\ntier: import\n---\n# Entry\n",
        "process/git.md": "---\ntier: action\nwhen:\n  - git: write\n---\ngit\n",
        "contexts/personal/context.md": "---\ntier: session\nmatch: {default: true}\n---\nhub\n",
        "ext/rows.py": """
from desk import ext

@ext.on("SessionStart")
def rows(ev):
    if ev.header is not None:
        ev.header.row("чекаут", "main · чисто", "🌿", "git-branch")
        ev.header.signal("мета", "много", "🔁", "repeat")
""",
    }

    def tearDown(self):
        from desk import ext
        for fns in ext.EVENTS.values():
            fns[:] = [f for f in fns if not f.__module__.startswith("desk_ext_")]
        super().tearDown()

    def start(self):
        return ctx_of(run_hook({"hook_event_name": "SessionStart", "source": "startup", "session_id": "h1",
                                "cwd": str(self.tmp)}, "--slot", "0", "--slots", "2"))

    def test_core_header_with_extension_rows(self):
        text = self.start()
        self.assertIn("desk_session_header", text)
        self.assertIn("│ 📦 подача", text)
        self.assertIn("за 1 триггером", text)
        self.assertIn("│ 🌿 чекаут    main · чисто", text)
        self.assertIn("├─ сигналы", text)
        self.assertIn("ti-repeat", text)

    def test_header_off_in_config(self):
        self.write({"desk.yml": "header: false\n"})
        text = self.start()
        self.assertNotIn("desk_session_header", text)
        self.assertIn("Активный контекст", text)
