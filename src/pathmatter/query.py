"""JSON-compatible, read-only vault path queries."""

import os
from collections.abc import Mapping
from pathlib import Path

from .documents import read_document
from .patterns import PathPattern, PatternError, validate_relative_directory


class QueryError(ValueError):
    """Invalid query or vault configuration."""


def query_documents(vault_root: str | Path, query: Mapping[str, object]) -> list[dict]:
    """Return Markdown documents selected by a JSON-compatible query object."""
    if not isinstance(query, Mapping):
        raise QueryError("query must be a JSON object")
    unknown = set(query) - {"path", "scope", "includeBody", "includeAbsolutePath"}
    if unknown:
        raise QueryError(f"unsupported query keys: {', '.join(sorted(unknown))}")
    path_pattern = query.get("path")
    if path_pattern is not None and not isinstance(path_pattern, str):
        raise QueryError("path must be a string")
    try:
        pattern = PathPattern(path_pattern) if path_pattern is not None else None
    except PatternError as error:
        raise QueryError(str(error)) from error
    scope = query.get("scope", {})
    if not isinstance(scope, Mapping):
        raise QueryError("scope must be an object")
    if set(scope) - {"directory", "mode"}:
        raise QueryError("scope supports only directory and mode")
    directory = scope.get("directory", "")
    try:
        validate_relative_directory(directory)
    except PatternError as error:
        raise QueryError(str(error)) from error
    mode = scope.get("mode", "descendants")
    if mode not in ("children", "descendants"):
        raise QueryError("scope.mode must be children or descendants")
    for name in ("includeBody", "includeAbsolutePath"):
        if name in query and not isinstance(query[name], bool):
            raise QueryError(f"{name} must be a boolean")

    root = Path(vault_root)
    if root.is_symlink() or not root.is_dir():
        raise QueryError("vault root must be an existing, non-symlink directory")
    root = root.absolute()
    scoped_path = root.joinpath(*directory.split("/")) if directory else root
    if scoped_path.is_symlink() or not scoped_path.is_dir():
        raise QueryError("scope directory must be an existing, non-symlink directory")
    # Reject symlinks in intermediate scope segments as well.
    current = root
    for part in directory.split("/") if directory else []:
        current = current / part
        if current.is_symlink():
            raise QueryError("scope directory must not pass through a symlink")

    documents = []
    for parent, directories, filenames in os.walk(scoped_path, followlinks=False):
        directories[:] = sorted(
            name for name in directories if not (Path(parent) / name).is_symlink()
        )
        if mode == "children":
            directories.clear()
        for filename in sorted(filenames):
            if not filename.endswith(".md"):
                continue
            physical = Path(parent) / filename
            if physical.is_symlink() or not physical.is_file():
                continue
            relative = physical.relative_to(root).as_posix()
            captures = pattern.match(relative) if pattern else {}
            if captures is None:
                continue
            document = read_document(
                physical,
                relative,
                include_body=query.get("includeBody", False),
                include_absolute_path=query.get("includeAbsolutePath", False),
            )
            document["params"] = captures
            documents.append(document)
    return sorted(documents, key=lambda document: document["path"])
