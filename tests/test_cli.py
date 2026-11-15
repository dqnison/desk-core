import contextlib
import io
import json
import os
from pathlib import Path

from desk import cli, lint, measure, transcripts
from tests.helpers import HomeCase, git_checkout

ROOT = Path(__file__).resolve().parent.parent


def run_cli(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(list(argv))
    return code, out.getvalue()


class TemplateTest(HomeCase):
    def test_template_lints_clean(self):
        self.assertEqual(lint.run(ROOT / "template"), [])

    def test_init_copies_template_and_prints_imports(self):
        target = self.tmp / "newdesk"
        code, out = run_cli("init", str(target))
        self.assertEqual(code, 0)
        self.assertTrue((target / "process/git.md").exists())
        self.assertIn(f"@{target / 'desk.md'}", out)
        code, out = run_cli("init", str(target))        # not empty: nothing overwritten
        self.assertIn("не пуст", out)

    def test_init_hooks_block_matches_plugin(self):
        code, out = run_cli("init", "--hooks")
        block = json.loads(out)["hooks"]
        plugin = json.loads((ROOT / "hooks/hooks.json").read_text())["hooks"]
        self.assertEqual(set(block), set(plugin))
        for ev in plugin:
            self.assertEqual([h.get("matcher") for h in block[ev]], [h.get("matcher") for h in plugin[ev]])
            self.assertEqual(len(block[ev][0]["hooks"]), len(plugin[ev][0]["hooks"]))

    def test_why_on_template(self):
        os.environ["DESK_HOME"] = str(ROOT / "template")
        code, out = run_cli("why", 'git commit -m "Fixed stuff."', "--cwd", str(self.tmp))
        self.assertIn("process/git.md [action] ← git commit", out)
        self.assertIn("deny process/git.md#2", out)
        self.assertIn("Итог хука (первый вызов в сессии): deny", out)
        code, out = run_cli("why", "git push --force origin main", "--cwd", str(self.tmp))
        self.assertIn("deny no-force-push", out)
        code, out = run_cli("why", "ls", "--cwd", str(self.tmp))
        self.assertIn("Ни одно правило не сработало", out)
        self.assertIn("Итог хука (первый вызов в сессии): ничего", out)

    def test_why_repo_rule_and_ask(self):
        os.environ["DESK_HOME"] = str(ROOT / "template")
        api = git_checkout(self.tmp / "shop-api", "git@git.example.com:acme/shop-api.git")
        code, out = run_cli("why", "billing/pay.py", "--tool", "Edit", "--content", "x", "--cwd", str(api))
        self.assertIn("Контекст: example", out)
        self.assertIn("ask  contexts/example/projects/shop-api/sensitive.md#1", out)
        self.assertIn("Итог хука (первый вызов в сессии): ask", out)


class LintTest(HomeCase):
    files = {
        "desk.md": "---\ntier: import\n---\n# D\n[ok](process/a.md#раздел-один) [bad](nope.md) [x](process/a.md#нет)"
                   " [dash](process/a.md#время-в-api-utc)\n",
        "process/a.md": "---\ntier: action\nwhen:\n  - comand: x\n---\n## Раздел один\n### Время в API — UTC\n",
        "process/b.md": "---\ntier: action\n---\n",
        "process/c.md": "---\ntier: link\nwhen:\n  - tool: Bash\n---\n",
        "process/d.md": "---\ntier: action\nwhen:\n  - command: '('\nguard:\n  - deny: x\n---\n",
        "process/e.md": "---\ntier: sometimes\n---\n",
        "process/f.md": "---\ntier: [x\n---\n",
        "process/big.md": "---\ntier: action\nwhen:\n  - tool: Bash\n---\n" + "x" * 13000,
        "contexts/a/context.md": "---\nmatch: {default: true, host: x}\n---\n",
        "contexts/b/context.md": "---\nmatch: {default: true}\n---\n",
        "contexts/b/place-words.txt": "Roadrunner\n",
        "process/leak.md": "---\ntier: link\n---\nship it to roadrunner\n",
    }

    def test_problems(self):
        got = {(rel, check) for rel, _, check, _ in lint.run(self.home)}
        expected = {
            ("desk.md", "links"),                 # nope.md and the missing anchor
            ("process/a.md", "when"),             # unknown key
            ("process/b.md", "when"),             # action without when
            ("process/c.md", "when"),             # link with when
            ("process/d.md", "when"),             # bad regex
            ("process/d.md", "guard"),            # no why
            ("process/e.md", "frontmatter"),
            ("process/f.md", "frontmatter"),
            ("process/big.md", "budget"),
            ("contexts/a/context.md", "contexts"),
            ("contexts", "contexts"),             # two defaults
            ("process/leak.md", "place-words"),
        }
        self.assertEqual(got, expected)
        links = [m for rel, _, c, m in lint.run(self.home) if c == "links"]
        self.assertEqual(len(links), 2)

    def test_imports_against_entry(self):
        entry = self.tmp / "CLAUDE.md"
        entry.write_text(f"@{self.home / 'process/c.md'}\n")
        os.environ["DESK_ENTRY"] = str(entry)
        try:
            got = {(rel, m.split(" ")[0]) for rel, _, c, m in lint.run(self.home) if c == "imports"}
        finally:
            del os.environ["DESK_ENTRY"]
        self.assertEqual(got, {("desk.md", "import"), ("process/c.md", "@-imported")})


class ThreadsLintTest(HomeCase):
    files = {
        "now.md": "# Нити\n\n## Ok\n→ [ok.md](threads/ok.md)\n\n## Done\n→ [done.md](threads/done.md)\n",
        "threads/ok.md": "---\nstatus: open\n---\n# Ok\n\n## Дальше\nx\n",
        "threads/lost.md": "---\nstatus: open\n---\n# Lost\n",
        "threads/done.md": "---\nstatus: closed\nclosed: 2026-11-01\n---\n# Done\n\n**Закрыта 2026-11-01.** x\n",
        "contexts/acme/now.md": "# Нити места\n\n## Big\n" + "x" * 5000 + "\n→ [a.md](threads/a.md)\n",
        "contexts/acme/threads/a.md": "---\nstatus: open\n---\n# A\n\n## Дальше\ny\n",
    }

    def test_threads_and_freshness(self):
        got = {(rel, check, msg.split(" ")[0]) for rel, _, check, msg in lint.run(self.home)
               if check in ("threads", "freshness")}
        self.assertEqual(got, {
            ("threads/lost.md", "threads", "открытая"),       # not in now.md, no «Дальше»
            ("threads/lost.md", "threads", "у"),
            ("now.md", "threads", "закрытая"),               # a closed thread still listed
            ("contexts/acme/now.md", "freshness", "contexts/acme/now.md"),   # over 4 KB
        })
        from desk import threads
        self.assertEqual([p.name for p in threads.open_threads(self.home)], ["lost.md", "ok.md", "a.md"])


def rec(type_, **kw):
    return json.dumps({"type": type_, **kw})


class MeasureTest(HomeCase):
    files = {
        "process/git.md": (ROOT / "template/process/git.md").read_text(),
        "process/threads.md": (ROOT / "template/process/threads.md").read_text(),
        "contexts/acme/context.md": "---\nmatch: {default: true}\n---\n",
        "contexts/acme/place-words.txt": "Roadrunner\n",
    }

    def transcript(self, name, lines):
        d = self.tmp / "projects" / "-w"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}.jsonl").write_text("\n".join(lines) + "\n")

    def tool(self, tid, name, inp, cwd="/w"):
        return rec("assistant", cwd=cwd, timestamp="2026-11-10T10:00:00Z",
                   message={"content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]})

    def hook(self, text):
        return rec("attachment", attachment={"type": "hook_additional_context", "hookEvent": "PreToolUse",
                                             "content": [text], "toolUseID": "x"})

    def setUp(self):
        super().setUp()
        self.transcript("s1", [
            self.tool("t1", "Bash", {"command": "git commit -m 'fix(no-ref): add cart'"}),
            self.hook("[desk] Сработало: …\n--- process/git.md ---\nbody"),
            self.tool("t2", "Bash", {"command": "git commit -m 'Fixed stuff.'"}),
            self.tool("t3", "Write", {"file_path": str(self.home / "now.md"), "content": "x"}),
            self.tool("t4", "Write", {"file_path": str(self.home / "process/new.md"), "content": "to Roadrunner"}),
        ])
        self.transcript("s2", [
            rec("user", cwd="/w", timestamp="2026-11-11T10:00:00Z", message={"content": "сделай коммит"}),
            self.tool("t1", "Bash", {"command": "ls"}),
        ])

    def records(self):
        return measure.load_records(self.tmp / "projects", days=None)

    def test_iter_session(self):
        r = transcripts.iter_session(self.tmp / "projects/-w/s1.jsonl")
        self.assertEqual([e["kind"] for e in r["events"]],
                         ["tool_use", "attachment", "tool_use", "tool_use", "tool_use"])
        self.assertEqual(r["cwd"], "/w")
        self.assertEqual({e["cwd"] for e in r["events"] if e["kind"] == "tool_use"}, {"/w"})

    def test_reach(self):
        from desk import home
        got = measure.reach(home.load_rules(self.home), self.records(), self.home)
        self.assertEqual(got["process/git.md"], (2, 1))       # s2: the prompt fired, nothing delivered
        self.assertEqual(got["process/threads.md"], (1, 0))

    def test_compliance(self):
        from desk import home
        got = measure.compliance(home.load_rules(self.home), self.records(), self.home)
        self.assertEqual(got["process/git.md#1"][1:3], (2, 2))     # no attribution in either commit
        self.assertEqual(got["process/git.md#2"][1:3], (1, 2))     # one bad subject
        self.assertEqual(got["place-words"][1:3], (1, 2))     # now.md clean, new.md leaks


class TriggerReachTest(MeasureTest):
    files = {**MeasureTest.files,
             "desk.yml": "triggers:\n  commit:\n    - git: write\n  deploy:\n    - command: '\\bkubectl\\b'\n",
             "process/c.md": "---\ntier: action\nwhen: [commit]\n---\nc body\n",
             "contexts/other/context.md": "---\nmatch: {remote: other.example.com}\n---\n",
             "contexts/other/o.md": "---\ntier: action\nwhen: [commit]\n---\no body\n"}
    test_iter_session = test_reach = test_compliance = None    # MeasureTest's own, run there

    def test_trigger_reach(self):
        from desk import home
        self.transcript("s3", [
            self.tool("t1", "Bash", {"command": "git commit -m 'fix(no-ref): x'"}),
            self.hook("[desk] Сработало: …\n--- process/c.md ---\nc body"),
        ])
        self.transcript("s4", [     # a rule of another place reached the model: not this session's
            self.tool("t1", "Bash", {"command": "git commit -m 'fix(no-ref): y'"}),
            self.hook("[desk] Сработало: …\n--- contexts/other/o.md ---\no body"),
        ])
        got = measure.trigger_reach(home.load_rules(self.home), self.records(), self.home)
        self.assertEqual(got, {"commit": (3, 1)})     # s1 without c.md, s3 delivered it, s4 out of scope


class RepeatsTest(HomeCase):
    """An extra delivery is the same label from another hook event in one stretch."""
    files = {"process/git.md": "---\ntier: action\n---\n" + "x" * 100,
             "process/tests.md": "---\ntier: action\n---\nt\n",
             "process/loop.md": "---\ntier: session\n---\nl\n"}

    @staticmethod
    def att(delivery, *labels):
        text = "[desk] x\n" + "\n".join(f"--- {lab} ---\nbody" for lab in labels)
        return {"kind": "attachment", "text": text, "delivery": delivery}

    def test_phantoms_are_not_copies_real_repeat_is(self):
        git = ("PreToolUse", "t1")
        events = [
            self.att("SessionStart", "process/loop.md (часть 1 из 2)"),
            self.att("SessionStart", "process/loop.md (часть 1 из 2)", "process/loop.md (часть 2 из 2)"),
            self.att(git, "process/git.md"),
            self.att(git, "process/git.md"),          # the same call, a second record
            {"kind": "compact"},
            self.att("SessionStart", "process/loop.md (часть 1 из 2)"),   # needed again after compaction
            self.att(("PreToolUse", "t2"), "process/git.md"),
            self.att(("PreToolUse", "t3"), "process/git.md"),           # a real repeat
        ]
        out = measure.repeats([{"events": events, "cwd": str(self.home)}], self.home)
        self.assertEqual([(d["path"], d["copies"], d["extra_bytes"]) for d in out],
                         [("process/git.md", 1, (self.home / "process/git.md").stat().st_size)])

    def test_read_is_output_not_mention(self):
        events = [{"kind": "tool_use", "name": "Bash", "input": {"command": "git add process/tests.md"},
                   "cwd": str(self.home)},
                  self.att(("PreToolUse", "t1"), "process/tests.md")]
        rec = {"events": events, "cwd": str(self.home)}
        self.assertEqual(measure.repeats([rec], self.home), [])
        events[0]["input"]["command"] = "cat process/tests.md"
        self.assertEqual(measure.repeats([rec], self.home)[0]["after_read"], 1)


class EntryScriptsTest(HomeCase):
    """bin/desk-hook and bin/desk run as the plugin runs them: a fresh process."""

    def test_hook_script_end_to_end(self):
        import subprocess
        import sys
        env = {**os.environ, "DESK_HOME": str(ROOT / "template")}
        data = {"hook_event_name": "PreToolUse", "session_id": "e2e", "cwd": str(self.tmp),
                "tool_name": "Bash", "tool_input": {"command": "git commit -m 'Fixed stuff.'"}}
        p = subprocess.run([sys.executable, str(ROOT / "bin/desk-hook")], input=json.dumps(data),
                           capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        out = json.loads(p.stdout)["hookSpecificOutput"]
        self.assertEqual(out["permissionDecision"], "deny")
        p = subprocess.run([sys.executable, str(ROOT / "bin/desk"), "lint"], capture_output=True,
                           text=True, env=env, timeout=30)
        self.assertEqual((p.returncode, p.stdout.strip()), (0, "lint: чисто"))

    def test_hook_never_breaks_on_garbage(self):
        import subprocess
        import sys
        for stdin in ("not json", json.dumps({"hook_event_name": "PreToolUse", "tool_input": "x"})):
            p = subprocess.run([sys.executable, str(ROOT / "bin/desk-hook")], input=stdin,
                               capture_output=True, text=True, timeout=30,
                               env={**os.environ, "DESK_HOME": str(self.tmp / "missing")})
            self.assertEqual(p.returncode, 0, p.stderr)
