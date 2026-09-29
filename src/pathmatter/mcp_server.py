"""Local STDIO MCP tools for one fixed Markdown vault."""

import argparse
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from .query import QueryError, get_document, query_documents
from .query_reference import QUERY_LANGUAGE_REFERENCE
from .search import SearchError, search_notes
from .writes import (
    WriteError,
    WriteValidationError,
    create_document,
    delete_document,
    update_document,
)


def _call(operation: Callable[[], Any]) -> Any:
    try:
        return operation()
    except WriteValidationError as error:
        raise ToolError(json.dumps({"diagnostics": error.preview["diagnostics"]})) from error
    except (QueryError, SearchError, WriteError) as error:
        raise ToolError(str(error)) from error


def build_server(vault_root: str | Path) -> FastMCP:
    """Build tools bound to one existing, non-symlink vault directory."""
    root = Path(vault_root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("vault root must be an existing, non-symlink directory")
    root = root.absolute()
    server = FastMCP(
        "pathmatter",
        instructions=(
            "Read and write Markdown documents only in the configured vault. "
            "The query_documents tool description is the query language reference. "
            "Use search_notes to retrieve relevant body passages. "
            "Use get_document before updating or deleting a specific document. "
            "Create and update validate path rules and schemas. "
            "Delete moves a document to recoverable vault-local trash."
        ),
    )

    @server.tool(
        name="query_documents",
        title="Query documents",
        description=QUERY_LANGUAGE_REFERENCE,
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
    )
    def query_documents_tool(query: dict[str, Any]) -> dict[str, Any]:
        return {"documents": _call(lambda: query_documents(root, query))}

    @server.tool(
        name="get_document",
        title="Get document",
        description="Read one Markdown document by its vault-relative .md path, including its body.",
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
    )
    def get_document_tool(path: str) -> dict[str, Any]:
        return _call(lambda: get_document(root, path))

    @server.tool(
        name="search_notes",
        title="Search note bodies",
        description=(
            "Search Markdown bodies in this vault. Choose semantic, lexical, or hybrid mode. "
            "Optional scope is a Pathmatter query object using path, scope, where, or "
            "bodyContains; matching full vault-relative paths restrict retrieval. "
            "Returns excerpts with 1-based line numbers within each body. "
            "The local index refreshes selected files before searching."
        ),
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
    )
    def search_notes_tool(
        text: str,
        mode: str,
        scope: dict[str, Any] | None = None,
        limit: int = 8,
    ) -> dict[str, Any]:
        return _call(lambda: search_notes(root, text, mode, scope, limit))

    @server.tool(
        name="create_document",
        title="Create document",
        description="Create one new Markdown document with validated frontmatter and optional body.",
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, openWorldHint=False
        ),
    )
    def create_document_tool(
        path: str, frontmatter: dict[str, Any], body: str = ""
    ) -> dict[str, Any]:
        return _call(
            lambda: create_document(
                root, {"path": path, "frontmatter": frontmatter, "body": body}
            )
        )

    @server.tool(
        name="update_document",
        title="Update document",
        description=(
            "Patch frontmatter using $set and $unset, or replace the body. "
            "Omit body to preserve it."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=True, openWorldHint=False
        ),
    )
    def update_document_tool(
        path: str, update: dict[str, Any] | None = None, body: str | None = None
    ) -> dict[str, Any]:
        request: dict[str, Any] = {"path": path}
        if update is not None:
            request["update"] = update
        if body is not None:
            request["body"] = body
        return _call(lambda: update_document(root, request))

    @server.tool(
        name="delete_document",
        title="Delete document",
        description=(
            "Move one Markdown document to recoverable vault-local trash. "
            "Returns its trash path for manual recovery."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=True, openWorldHint=False
        ),
    )
    def delete_document_tool(path: str) -> dict[str, Any]:
        return _call(lambda: delete_document(root, {"path": path}))

    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pathmatter-mcp")
    parser.add_argument("vault", type=Path, help="directory containing the Markdown vault")
    args = parser.parse_args(argv)
    try:
        server = build_server(args.vault)
    except ValueError as error:
        parser.error(str(error))
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
