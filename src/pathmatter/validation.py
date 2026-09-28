"""Read-only validation of all Markdown documents in a vault."""

from pathlib import Path

from .query import query_documents


def validate_vault(vault_root: str | Path) -> list[dict]:
    """Return paths and diagnostics for documents with parsing or rule errors."""
    documents = query_documents(vault_root, {"includeAbsolutePath": True})
    return [
        {
            "path": document["path"],
            "absolutePath": document["absolutePath"],
            "diagnostics": document["diagnostics"],
        }
        for document in documents
        if document["diagnostics"]
    ]
