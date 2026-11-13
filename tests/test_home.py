from pathlib import Path

from desk import home
from tests.helpers import HomeCase, git_checkout


class HomeTest(HomeCase):
    files = {
        "desk.md": "---\ntier: import\n---\n# Desk\n",
        "now.md": "# no tier: not a rule\n",
        "process/git.md": "---\ntier: action\nwhen:\n  - git: write\n---\n# Git\n",
        "process/broken.md": "---\ntier: [unterminated\n---\n",
        "skills/x/SKILL.md": "---\nname: x\ndescription: &anchor not ours\n---\n",
        "work/DEMO-123/notes.md": "---\ntier: action\n---\nscratch is not walked\n",
        "contexts/acme/context.md": "---\ntier: session\nmatch: {remote: git.acme.example.com}\n---\n# Acme\n",
        "contexts/acme/projects/shop-api/sensitive.md": "---\ntier: action\nwhen:\n  - paths: ['billing/**']\n---\n",
        "contexts/personal/context.md": "---\ntier: session\nmatch: {default: true}\n---\n# Personal\n",
    }

    def test_rules_are_files_with_tier(self):
        rules = {r.rel: r for r in home.load_rules(self.home)}
        self.assertNotIn("now.md", rules)
        self.assertNotIn("work/DEMO-123/notes.md", rules)
        self.assertIsNotNone(rules["process/broken.md"].error)
        self.assertNotIn("skills/x/SKILL.md", rules)      # unparsable but not a rule: ignored
        self.assertEqual(rules["process/git.md"].when, [{"git": "write"}])
        s = rules["contexts/acme/projects/shop-api/sensitive.md"]
        self.assertEqual((s.context, s.repo, s.layer), ("acme", "shop-api", 0))
        self.assertEqual((rules["contexts/acme/context.md"].layer, rules["desk.md"].layer), (1, 2))

    def test_context_by_remote_default_and_folder(self):
        ctxs = home.load_contexts(self.home)
        remotes = "origin\tgit@git.acme.example.com:team/shop-api.git (fetch)\n"
        self.assertEqual(home.pick_context(ctxs, "/src/shop-api", remotes)[0].name, "acme")
        self.assertEqual(home.pick_context(ctxs, "/src/pet", "")[0].name, "personal")
        # a session inside contexts/<name>/ of DESK_HOME is that context, whatever the remote
        self.assertEqual(home.pick_context(ctxs, self.home / "contexts/acme/projects", "")[0].name, "acme")

    def test_no_default_context(self):
        self.write({"contexts/personal/context.md": "---\ntier: session\n---\n"})
        ctx, basis = home.pick_context(home.load_contexts(self.home), "/src/pet", "")
        self.assertIsNone(ctx)

    def test_path_match(self):
        self.write({"contexts/lab/context.md": "---\nmatch:\n  path: ['/src/lab/*']\n---\n"})
        ctx, basis = home.pick_context(home.load_contexts(self.home), "/src/lab/x/deep", "")
        self.assertEqual((ctx.name, basis), ("lab", "путь сессии"))

    def test_session_from_hook_input(self):
        repo = git_checkout(self.tmp / "src" / "shop-api", "git@git.acme.example.com:team/shop-api.git")
        s = home.session_from({"cwd": str(repo), "session_id": "s1"}, self.home)
        self.assertEqual((s.context.name, s.repo, s.in_home), ("acme", "shop-api", False))
        self.assertEqual(s.known_repos(), ["shop-api"])
        rules = {r.rel: r for r in home.load_rules(self.home)}
        self.assertTrue(home.in_scope(rules["contexts/acme/projects/shop-api/sensitive.md"], s))
        self.assertFalse(home.in_scope(rules["contexts/personal/context.md"], s))

    def test_session_dir_follows_transcript_folder(self):
        sub = self.tmp / "proj" / "sub"
        sub.mkdir(parents=True)
        data = {"cwd": str(sub), "transcript_path": f"/x/{home.path_slug(self.tmp / 'proj')}/t.jsonl"}
        self.assertEqual(home.session_dir(data, project_dir=""), str(self.tmp / "proj"))
        self.assertEqual(home.session_dir({"cwd": str(sub)}, project_dir=""), str(sub))

    def test_skip_in(self):
        rule = home.Rule(Path("x"), "x.md", {"tier": "session", "skip_in": ["desk", "personal"]})
        s = home.session_from({"cwd": str(self.home)}, self.home)
        self.assertTrue(home.skipped(rule, s))            # session inside DESK_HOME
        s = home.session_from({"cwd": "/"}, self.home)
        self.assertTrue(home.skipped(rule, s))            # default context is personal
        s.context = None
        self.assertFalse(home.skipped(rule, s))

    def test_repo_name_from_remote(self):
        self.assertEqual(home.repo_name("origin\thttps://example.com/acme/web.git (fetch)"), "web")
        self.assertEqual(home.repo_name("origin\tgit@example.com:acme/web (fetch)"), "web")
        self.assertEqual(home.repo_name(""), "")
