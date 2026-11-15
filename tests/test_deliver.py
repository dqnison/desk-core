import io
import json
from pathlib import Path

from desk import deliver as dl, hook, home
from tests.helpers import HomeCase, git_checkout


def big(n_sections, size):
    return "".join(f"## S{i}\n" + ("x" * size) + "\n" for i in range(n_sections))


class UnitsTest(HomeCase):
    files = {
        "process/small.md": "---\ntier: action\n---\n# Small\ntext\n",
        "process/big.md": "---\ntier: action\n---\n# Big\n" + big(4, 5000),
        "process/huge-section.md": "---\ntier: action\n---\n## One\n" + ("line\n" * 4000),
    }

    def rule(self, rel):
        return next(r for r in home.load_rules(self.home) if r.rel == rel)

    def test_small_rule_is_one_unit(self):
        u = dl.units(self.rule("process/small.md"))
        self.assertEqual([x.label for x in u], ["process/small.md"])
        self.assertEqual(u[0].text, "# Small\ntext")

    def test_big_rule_cut_by_sections_into_labelled_parts(self):
        u = dl.units(self.rule("process/big.md"))
        self.assertEqual(len(u), 2)
        self.assertEqual(u[1].label, "process/big.md (часть 2 из 2)")
        self.assertTrue(all(x.size <= dl.BUDGET for x in u))
        self.assertTrue(u[1].text.startswith("## S"))

    def test_section_larger_than_budget_cut_by_lines(self):
        u = dl.units(self.rule("process/huge-section.md"))
        self.assertGreater(len(u), 1)
        self.assertTrue(all(x.size <= dl.BUDGET for x in u))

    def test_portions_number_files_as_the_hook_packs_them(self):
        rows, n = dl.portions([self.rule("process/small.md"), self.rule("process/big.md")])
        self.assertEqual(n, 2)                                  # big.md is two parts
        self.assertEqual(rows, {"process/small.md": 1, "process/big.md": 1})

    def test_registered_slots_from_settings(self):
        from desk import cli
        settings = self.tmp / "settings.json"
        settings.write_text(json.dumps(cli.hooks_block()))
        self.assertEqual(cli.registered_slots(settings), cli.SLOTS)
        self.assertEqual(cli.registered_slots(self.tmp / "none.json"), 1)

    def test_bundle_budget_and_state(self):
        rules = [self.rule("process/big.md"), self.rule("process/small.md")]
        state = dl.State("s1")
        send, rest = dl.bundle(rules, state)
        # part 2 does not fit after part 1, the small rule still does
        self.assertEqual([u.label for u in send], ["process/big.md (часть 1 из 2)", "process/small.md"])
        state.sent(send, rest)
        self.assertEqual(state.pending, ["process/big.md"])
        send, rest = dl.bundle(rules, state)
        state.sent(send, rest)
        self.assertEqual([u.label for u in send], ["process/big.md (часть 2 из 2)"])
        self.assertEqual(state.pending, [])
        self.assertTrue(state.fully_delivered(rules[0]))

    def test_reset_once_per_start(self):
        s = dl.State("s7", delivered={"a"})
        s.save()
        self.assertTrue(dl.reset({"session_id": "s7", "source": "clear"}, now=1000.0))
        dl.State("s7", delivered={"b"}).save()         # slot 1 delivered after the reset
        self.assertFalse(dl.reset({"session_id": "s7", "source": "clear"}, now=1010.0))
        self.assertEqual(dl.State.load("s7").delivered, {"b"})
        self.assertTrue(dl.reset({"session_id": "s7", "source": "compact"}, now=2000.0))

    def test_state_roundtrip_and_reset(self):
        s = dl.State("s1", delivered={"a"}, pending=["b"], gates={"g"}, asks={"k"})
        s.save()
        got = dl.State.load("s1")
        self.assertEqual((got.delivered, got.pending, got.gates, got.asks), ({"a"}, ["b"], {"g"}, {"k"}))
        self.assertFalse(dl.reset({"session_id": "s1", "source": "startup"}))
        self.assertTrue(dl.reset({"session_id": "s1", "source": "compact"}))
        self.assertEqual(dl.State.load("s1").delivered, set())

    def test_partition(self):
        rules = [self.rule("process/big.md"), self.rule("process/small.md"), self.rule("process/huge-section.md")]
        buckets, rest = dl.partition(rules, 2)
        self.assertEqual([len(b) for b in buckets], [2, 1])
        self.assertEqual([r.rel for r in rest], ["process/huge-section.md"])


def run_hook(data, *argv):
    out = io.StringIO()
    import contextlib
    with contextlib.redirect_stdout(out):
        hook.main(list(argv), stdin=io.StringIO(json.dumps(data)))
    text = out.getvalue().strip()
    return json.loads(text) if text else {}


def ctx_of(out):
    return (out.get("hookSpecificOutput") or {}).get("additionalContext", "")


def decision_of(out):
    return (out.get("hookSpecificOutput") or {}).get("permissionDecision")


class HookTest(HomeCase):
    files = {
        "desk.md": "---\ntier: import\n---\n# Entry\n",
        "process/loop.md": "---\ntier: session\nskip_in: [desk]\n---\n# Loop\nloop body\n",
        "process/git.md": "---\ntier: action\nwhen:\n  - git: write\n  - prompt: '\\bcommit'\n---\n# Git\ngit body\n",
        "process/logs.md": "---\ntier: action\nwhen:\n  - tool: 'mcp__metrics*'\n    gate: true\n  - prompt: 'логи'\n---\nlogs body\n",
        "process/threads.md": "---\ntier: action\nwhen:\n  - paths: ['**/now.md']\n---\nthreads body\n",
        "contexts/acme/context.md": "---\ntier: session\nmatch: {remote: git.acme.example.com}\n---\n# Acme hub\n",
        "contexts/acme/tracker.md": "---\ntier: action\nwhen:\n  - command: 'tracker-cli'\n---\nacme tracker\n",
        "contexts/acme/projects/shop-api/local.md": "---\ntier: session\n---\nshop-api local\n",
        "contexts/acme/projects/web/local.md": "---\ntier: session\n---\nweb local\n",
        "contexts/personal/context.md": "---\ntier: session\nmatch: {default: true}\n---\n# Personal hub\n",
    }

    def setUp(self):
        super().setUp()
        self.api = git_checkout(self.tmp / "src/shop-api", "git@git.acme.example.com:team/shop-api.git")
        self.web = git_checkout(self.tmp / "src/web", "git@git.acme.example.com:team/web.git")

    def pre(self, cmd=None, sid="s1", cwd=None, tool="Bash", **inp):
        data = {"hook_event_name": "PreToolUse", "session_id": sid, "cwd": str(cwd or self.api),
                "tool_name": tool, "tool_input": {"command": cmd} if cmd is not None else inp}
        return run_hook(data)

    def test_session_start_hub_first_and_scope(self):
        head = ctx_of(run_hook({"hook_event_name": "SessionStart", "source": "startup", "session_id": "s1",
                                "cwd": str(self.api)}, "--slot", "0", "--slots", "2"))
        self.assertIn("Активный контекст: acme", head)
        self.assertNotIn("Acme hub", head)
        out = run_hook({"hook_event_name": "SessionStart", "source": "startup", "session_id": "s1",
                        "cwd": str(self.api)}, "--slot", "1", "--slots", "2")
        text = ctx_of(out)
        self.assertNotIn("Активный контекст", text)
        self.assertLess(text.index("Acme hub"), text.index("shop-api local"))
        self.assertIn("loop body", text)
        self.assertNotIn("web local", text)
        self.assertNotIn("Personal hub", text)
        # slot 2 has nothing: everything fit into slot 1
        self.assertEqual(run_hook({"hook_event_name": "SessionStart", "source": "startup",
                                   "session_id": "s1", "cwd": str(self.api)}, "--slot", "2", "--slots", "2"), {})

    def test_session_start_silent_on_resume_and_skip_in_desk(self):
        self.assertEqual(run_hook({"hook_event_name": "SessionStart", "source": "resume",
                                   "session_id": "s1", "cwd": str(self.api)}), {})
        text = ctx_of(run_hook({"hook_event_name": "SessionStart", "source": "startup",
                                "session_id": "s2", "cwd": str(self.home)}))
        self.assertNotIn("loop body", text)       # skip_in: [desk]
        self.assertIn("Personal hub", text)        # default context

    def test_action_once_per_session_and_reset_on_compact(self):
        out = self.pre("git commit -m x")
        self.assertIn("git body", ctx_of(out))
        self.assertIn("process/git.md ← git commit", ctx_of(out))
        self.assertEqual(self.pre("git push"), {})
        run_hook({"hook_event_name": "SessionStart", "source": "compact", "session_id": "s1",
                  "cwd": str(self.api)})
        self.assertIn("git body", ctx_of(self.pre("git push")))

    def test_context_rule_only_in_its_context(self):
        self.assertIn("acme tracker", ctx_of(self.pre("tracker-cli list")))
        self.assertEqual(self.pre("tracker-cli list", sid="s9", cwd="/"), {})

    def test_gate_stops_first_call_then_passes(self):
        out = self.pre(tool="mcp__metrics__query", q="up")
        self.assertEqual(decision_of(out), "deny")
        self.assertIn("logs body", ctx_of(out))
        self.assertEqual(self.pre(tool="mcp__metrics__query", q="up"), {})

    def test_prompt_delivers_and_opens_gate(self):
        out = run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "s1", "cwd": str(self.api),
                        "prompt": "глянь логи"})
        self.assertIn("logs body", ctx_of(out))
        self.assertIn("UserPromptSubmit", ctx_of(out))
        self.assertEqual(self.pre(tool="mcp__metrics__query", q="up"), {})   # already in context: no gate

    def test_second_repo_session_rules_ride_with_the_call(self):
        out = self.pre(f"cd {self.web} && ls")
        self.assertIn("web local", ctx_of(out))
        self.assertNotIn("shop-api local", ctx_of(out))

    def test_queue_carries_what_did_not_fit(self):
        self.write({"process/git.md": "---\ntier: action\nwhen:\n  - git: write\n---\n" + big(3, 5000)})
        out = self.pre("git commit -m x")
        self.assertIn("часть 1 из 2", ctx_of(out))
        self.assertIn("Остальное придёт", ctx_of(out))
        out = self.pre("ls")
        self.assertIn("часть 2 из 2", ctx_of(out))
        self.assertEqual(self.pre("ls"), {})

    def test_read_in_full_counts_as_delivered(self):
        rule = str(self.home / "process/git.md")
        run_hook({"hook_event_name": "PostToolUse", "session_id": "s1", "cwd": str(self.api),
                  "tool_name": "Read", "tool_input": {"file_path": rule}, "tool_response": {}})
        self.assertEqual(self.pre("git commit -m x"), {})
        # a partial read does not count
        run_hook({"hook_event_name": "PostToolUse", "session_id": "s2", "cwd": str(self.api),
                  "tool_name": "Read", "tool_input": {"file_path": rule, "limit": 3}, "tool_response": {}})
        self.assertIn("git body", ctx_of(self.pre("git commit -m x", sid="s2")))

    def test_post_tool_sees_unpredicted_write(self):
        git_checkout(self.home)
        cmd = f"python3 make.py --out {self.home}"        # writes now.md, the text does not say so
        base = {"session_id": "s1", "cwd": str(self.api), "tool_name": "Bash",
                "tool_input": {"command": cmd}, "tool_use_id": "t1"}
        self.assertEqual(run_hook({**base, "hook_event_name": "PreToolUse"}), {})
        (self.home / "contexts/acme/now.md").write_text("# now\n", encoding="utf-8")
        out = run_hook({**base, "hook_event_name": "PostToolUse", "tool_response": {}})
        self.assertIn("threads body", ctx_of(out))
        self.assertIn("contexts/acme/now.md", ctx_of(out))

    def test_snapshot_roots_leave_out_an_untouched_home(self):
        git_checkout(self.home)
        s = home.session_from({"cwd": str(self.api)}, self.home)
        from desk import when as wh
        roots = hook._roots(wh.Call(tool="Bash", input={"command": "make test"}, cwd=str(self.api)), s)
        self.assertEqual(roots, {str(self.api)})
        roots = hook._roots(wh.Call(tool="Bash", input={"command": f"ls {self.home}/process"}, cwd=str(self.api)), s)
        self.assertEqual(roots, {str(self.api), str(self.home)})

    def test_status_snapshot_reads_odd_names(self):
        (self.api / "имя с пробелом.md").write_text("x")
        snap = hook.status_snapshot({str(self.api)})
        self.assertIn("имя с пробелом.md", snap[str(self.api)])

    def test_non_object_stdin_is_ignored(self):
        self.assertEqual(run_hook([1, 2]), {})

    def test_no_deny_switch_opens_gates(self):
        import os
        os.environ["DESK_NO_DENY"] = "1"
        try:
            self.assertNotEqual(decision_of(self.pre(tool="mcp__metrics__query", q="up")), "deny")
        finally:
            del os.environ["DESK_NO_DENY"]

    def test_quiet_env(self):
        import os
        os.environ["DESK_HOOKS_OFF"] = "1"
        self.assertEqual(self.pre("git commit -m x"), {})
