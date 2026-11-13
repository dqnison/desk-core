import unittest

from desk import frontmatter as fm


class FrontmatterTest(unittest.TestCase):
    def test_flat_keys_and_lists(self):
        text = "---\ntier: action\ntags: [a, 'b c']\nskip_in:\n  - desk\n  - personal\n---\nbody\n"
        self.assertEqual(fm.parse(text), {"tier": "action", "tags": ["a", "b c"],
                                          "skip_in": ["desk", "personal"]})

    def test_list_at_same_indent_as_key(self):
        self.assertEqual(fm.parse("---\nx:\n- a\n- b\n---\n"), {"x": ["a", "b"]})

    def test_sequence_of_mappings(self):
        text = """---
tier: action
when:
  - tool: Bash
    git: write
  - prompt: 'коммит|commit'
  - tool: ['mcp__metrics*', 'mcp__logs*']
    gate: true
guard:
  - deny: 'Co-Authored-By|Generated with'
    in: commit-message
    why: no attribution
---
"""
        got = fm.parse(text)
        self.assertEqual(got["when"], [
            {"tool": "Bash", "git": "write"},
            {"prompt": "коммит|commit"},
            {"tool": ["mcp__metrics*", "mcp__logs*"], "gate": True},
        ])
        self.assertEqual(got["guard"][0]["in"], "commit-message")

    def test_nested_mapping_and_flow_map(self):
        got = fm.parse("---\nmatch: {remote: 'git.example.com', default: false}\ninput:\n  path: '/logs'\n---\n")
        self.assertEqual(got, {"match": {"remote": "git.example.com", "default": False},
                               "input": {"path": "/logs"}})

    def test_regex_with_colon_hash_and_quotes(self):
        got = fm.parse("---\na: '^(feat|fix)\\(no-ref\\): [a-z]'\nb: \"x # not a comment\"  # comment\nc: it''s\n"
                       "d: 'it''s'\n---\n")
        self.assertEqual(got["a"], "^(feat|fix)\\(no-ref\\): [a-z]")
        self.assertEqual(got["b"], "x # not a comment")
        self.assertEqual(got["c"], "it''s")
        self.assertEqual(got["d"], "it's")

    def test_scalars(self):
        got = fm.parse("---\na: true\nb: 12\nc: ~\nd:\ne: 2026-11-13\n---\n")
        self.assertEqual(got, {"a": True, "b": 12, "c": None, "d": None, "e": "2026-11-13"})

    def test_no_frontmatter(self):
        self.assertEqual(fm.parse("# title\n"), {})
        self.assertEqual(fm.parse("---\nunterminated: x\n"), {})

    def test_errors(self):
        for bad in ("---\na: |\n  x\n---\n", "---\na: [1, 2\n---\n", "---\na: 1\na: 2\n---\n",
                    "---\na: 1\n    b: 2\n---\n", "---\njust text\n---\n"):
            with self.subTest(bad=bad), self.assertRaises(fm.FrontmatterError):
                fm.parse(bad)

    def test_body(self):
        self.assertEqual(fm.body("---\ntier: link\n---\n\n# T\ntext\n"), "# T\ntext")


if __name__ == "__main__":
    unittest.main()
