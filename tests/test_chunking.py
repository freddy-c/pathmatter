import tempfile
import unittest
from pathlib import Path

from pathmatter.chunking import chunk_markdown
from pathmatter.documents import read_document


def count_words(text: str) -> int:
    return len(text.split())


class ChunkingTests(unittest.TestCase):
    def test_short_body_is_one_exact_chunk_with_body_lines(self):
        body = "# Storage decision\r\n\r\nKeep the prototype filesystem based.\r\n"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "note.md"
            path.write_bytes(b"\xef\xbb\xbf---\r\ntitle: Storage\r\n---\r\n" + body.encode())
            document = read_document(
                path, "note.md", include_body=True, include_absolute_path=False
            )
        self.assertEqual(document["body"], body)
        self.assertEqual(document["frontmatter"], {"title": "Storage"})
        chunks = chunk_markdown(document["body"], count_words, max_tokens=20)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].text, body)
        self.assertEqual(chunks[0].heading, "Storage decision")
        self.assertEqual((chunks[0].body_start_line, chunks[0].body_end_line), (1, 3))

    def test_blocks_stay_intact_when_possible_and_cover_source(self):
        body = (
            "# Heading\n\nParagraph one.\n\n"
            "- first item\n- second item\n\n"
            "```py\nprint(1)\n```\n\n"
            "| a | b |\n| - | - |\n| 1 | 2 |\n"
        )
        chunks = chunk_markdown(body, count_words, max_tokens=16)
        self.assertEqual("".join(chunk.text for chunk in chunks), body)
        self.assertTrue(all(count_words(chunk.text) <= 16 for chunk in chunks))
        self.assertTrue(any("- first item\n- second item" in c.text for c in chunks))
        self.assertTrue(any("```py\nprint(1)\n```" in c.text for c in chunks))
        self.assertTrue(any("| a | b |\n| - | - |\n| 1 | 2 |" in c.text for c in chunks))
        for chunk in chunks:
            self.assertEqual(body[chunk.start_offset : chunk.end_offset], chunk.text)

    def test_oversized_block_splits_only_as_needed(self):
        body = "# Title\n\n" + "one two three four five six seven eight nine ten\n" * 4
        chunks = chunk_markdown(body, count_words, max_tokens=12)
        self.assertEqual("".join(chunk.text for chunk in chunks), body)
        self.assertTrue(all(count_words(chunk.text) <= 12 for chunk in chunks))
        self.assertEqual(chunks[0].body_start_line, 1)
        self.assertEqual(chunks[-1].body_end_line, 6)

    def test_long_single_line_can_share_one_source_line(self):
        body = "alpha beta gamma delta epsilon zeta eta theta"
        chunks = chunk_markdown(body, count_words, max_tokens=2)
        self.assertEqual("".join(chunk.text for chunk in chunks), body)
        self.assertTrue(all(chunk.body_start_line == chunk.body_end_line == 1 for chunk in chunks))

    def test_empty_body_has_no_chunks(self):
        self.assertEqual(chunk_markdown("\n\n", count_words), [])


if __name__ == "__main__":
    unittest.main()
