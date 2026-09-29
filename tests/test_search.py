import tempfile
import unittest
from pathlib import Path

from pathmatter.search import SearchError, index_vault, search_notes


class FakeEmbedder:
    identity = "test-embedding-v1"
    max_tokens = 256

    def token_count(self, text):
        return len(text.split())

    def embed(self, texts):
        return [
            [float(text.lower().count("alpha")), float(text.lower().count("beta")), 1.0]
            for text in texts
        ]


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.vault = self.base / "vault"
        self.vault.mkdir()
        self.index = self.base / "index"
        (self.vault / "projects").mkdir()
        (self.vault / "projects" / "one.md").write_text(
            "---\nstatus: active\n---\n# First\n\nalpha beta\n", encoding="utf-8"
        )
        (self.vault / "projects" / "two.md").write_text(
            "---\nstatus: done\n---\n# Second\n\nbeta gamma\n", encoding="utf-8"
        )

    def search(self, text="alpha", mode="hybrid", scope=None, limit=8):
        return search_notes(
            self.vault, text, mode, scope, limit,
            index_dir=self.index, embedder=FakeEmbedder(),
        )["results"]

    def test_modes_return_source_locations_and_bounded_results(self):
        for mode in ("semantic", "lexical", "hybrid"):
            with self.subTest(mode=mode):
                results = self.search(mode=mode, limit=1)
                self.assertEqual(len(results), 1)
                hit = results[0]
                self.assertEqual(hit["path"], "projects/one.md")
                self.assertEqual(hit["heading"], "First")
                self.assertEqual((hit["bodyStartLine"], hit["bodyEndLine"]), (1, 3))
                self.assertEqual(hit["excerpt"], "# First\n\nalpha beta\n")
                self.assertEqual(hit["retrievalMode"], mode)
                self.assertIn("distance" if mode == "semantic" else "score", hit)

    def test_path_and_frontmatter_scope_use_full_path(self):
        scope = {"path": "projects/*.md", "where": {"status": "done"}}
        self.assertEqual([r["path"] for r in self.search("beta", "lexical", scope)], ["projects/two.md"])
        self.assertEqual(self.search("alpha", "lexical", scope), [])
        self.assertEqual(self.search("beta", "semantic", {"path": "nothing/*.md"}), [])

    def test_rechecks_files_and_removes_old_chunks_after_edits_and_rename(self):
        self.assertEqual(len(self.search("alpha", "lexical")), 1)
        first = self.vault / "projects" / "one.md"
        first.write_text("---\nstatus: done\n---\n# Changed\n\ndelta\n", encoding="utf-8")
        self.assertEqual(self.search("alpha", "lexical"), [])
        self.assertEqual(self.search("delta", "lexical")[0]["heading"], "Changed")
        renamed = self.vault / "projects" / "renamed.md"
        first.rename(renamed)
        self.assertEqual([r["path"] for r in self.search("delta", "lexical")], ["projects/renamed.md"])
        renamed.unlink()
        self.assertEqual(self.search("delta", "lexical"), [])

    def test_index_reuses_unchanged_files_and_rebuilds(self):
        kwargs = {"index_dir": self.index, "embedder": FakeEmbedder()}
        self.assertEqual(index_vault(self.vault, **kwargs)["indexed"], 2)
        self.assertEqual(index_vault(self.vault, **kwargs)["indexed"], 0)
        self.assertEqual(index_vault(self.vault, rebuild=True, **kwargs)["indexed"], 2)

    def test_skips_trash_and_symlinks(self):
        trash = self.vault / ".pathmatter-trash"
        trash.mkdir()
        (trash / "old.md").write_text("secretword\n", encoding="utf-8")
        (self.vault / "linked.md").symlink_to(self.vault / "projects" / "one.md")
        self.assertEqual(self.search("secretword", "lexical"), [])
        self.assertEqual([r["path"] for r in self.search("alpha", "lexical")], ["projects/one.md"])

    def test_model_identity_uses_a_fresh_index(self):
        self.search("alpha", "lexical")

        class NewModel(FakeEmbedder):
            identity = "test-embedding-v2"

        result = index_vault(self.vault, index_dir=self.index, embedder=NewModel())
        self.assertEqual(result["indexed"], 2)

    def test_large_exact_path_scope_is_batched(self):
        for number in range(130):
            (self.vault / "projects" / f"bulk-{number:03}.md").write_text(
                "---\nstatus: bulk\n---\n# Bulk\n\nbeta\n", encoding="utf-8"
            )
        scope = {"path": "projects/*.md", "where": {"status": "bulk"}}
        for mode in ("semantic", "lexical", "hybrid"):
            with self.subTest(mode=mode):
                results = self.search("beta", mode, scope, 5)
                self.assertEqual(len(results), 5)
                self.assertTrue(all(result["path"].startswith("projects/bulk-") for result in results))

    def test_invalid_requests(self):
        for args in (("", "hybrid", None, 8), ("alpha", "other", None, 8), ("alpha", "hybrid", None, 21)):
            with self.subTest(args=args), self.assertRaises(SearchError):
                self.search(*args)
        with self.assertRaises(SearchError):
            self.search(scope={"projection": {"status": 1}})


if __name__ == "__main__":
    unittest.main()
