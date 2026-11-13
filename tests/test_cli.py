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
        "desk.md": "---\ntier: import\n---\n# D\n[ok](process/a.md#раздел-один) [bad](nope.md) [x](process/a.md#нет)\n",
        "process/a.md": "---\ntier: action\nwhen:\n  - comand: x\n---\n## Раздел один\n",
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
