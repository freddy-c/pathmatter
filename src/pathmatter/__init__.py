"""Query filesystem Markdown and frontmatter."""

__version__ = "0.1.0"

from .query import QueryError, query_documents

__all__ = ["QueryError", "query_documents"]
