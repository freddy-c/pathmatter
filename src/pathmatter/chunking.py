"""Markdown chunks with exact body source locations."""

from bisect import bisect_right
from collections.abc import Callable
from dataclasses import dataclass

from markdown_it import MarkdownIt
from semantic_text_splitter import MarkdownSplitter

_PARSER = MarkdownIt("commonmark").enable("table")


@dataclass(frozen=True)
class Chunk:
    text: str
    heading: str | None
    body_start_line: int
    body_end_line: int
    start_offset: int
    end_offset: int


def chunk_markdown(
    body: str,
    token_count: Callable[[str], int],
    *,
    max_tokens: int = 256,
    max_chars: int = 2000,
) -> list[Chunk]:
    """Pack Markdown blocks to the embedding limit, preserving original text."""
    if max_tokens < 1 or max_chars < 1:
        raise ValueError("chunk limits must be positive")
    if not body.strip():
        return []

    # The splitter handles block boundaries, packing and oversized blocks.
    # The secondary character budget keeps MCP excerpts bounded even for text
    # with many characters but few embedding tokens (such as long whitespace).
    def size(text: str) -> int:
        character_units = (len(text) * max_tokens + max_chars - 1) // max_chars
        return max(token_count(text), character_units)

    splitter = MarkdownSplitter.from_callback(size, max_tokens, trim=False)
    line_starts = [0]
    for line in body.splitlines(keepends=True):
        line_starts.append(line_starts[-1] + len(line))

    headings = []
    tokens = _PARSER.parse(body)
    for index, token in enumerate(tokens):
        if token.type == "heading_open" and token.map:
            headings.append(
                (line_starts[token.map[0]], tokens[index + 1].content.strip())
            )

    chunks: list[Chunk] = []
    heading_index = 0
    current_heading: str | None = None
    cursor = 0
    for start, text in splitter.chunk_indices(body):
        end = start + len(text)
        if start != cursor or body[start:end] != text:
            raise ValueError("Markdown splitter returned text outside the source body")
        while heading_index < len(headings) and headings[heading_index][0] <= start:
            current_heading = headings[heading_index][1]
            heading_index += 1
        if heading_index < len(headings) and headings[heading_index][0] < end:
            current_heading = headings[heading_index][1]
        chunks.append(
            Chunk(
                text=text,
                heading=current_heading,
                body_start_line=bisect_right(line_starts, start),
                body_end_line=bisect_right(line_starts, end - 1),
                start_offset=start,
                end_offset=end,
            )
        )
        cursor = end
    if cursor != len(body):
        raise ValueError("Markdown splitter omitted source text")
    return chunks
