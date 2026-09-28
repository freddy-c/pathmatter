"""Query filesystem Markdown and frontmatter."""

__version__ = "0.1.0"

from .query import QueryError, query_documents
from .validation import validate_vault
from .writes import (
    WriteError,
    WriteValidationError,
    create_document,
    delete_document,
    preview_document,
    update_document,
)

__all__ = [
    "QueryError",
    "WriteError",
    "WriteValidationError",
    "create_document",
    "delete_document",
    "preview_document",
    "query_documents",
    "update_document",
    "validate_vault",
]
