"""Query filesystem Markdown and frontmatter."""

__version__ = "0.1.0"

from .query import QueryError, query_documents
from .validation import validate_vault

__all__ = ["QueryError", "query_documents", "validate_vault"]
