"""JSON-compatible, read-only vault path queries."""

import json
import math
import os
from collections.abc import Mapping
from pathlib import Path

from .documents import read_document
from .filters import FilterError, matches, uses_only_system_fields, validate_filter
from .patterns import PathPattern, PatternError, validate_relative_directory


class QueryError(ValueError):
    """Invalid query or vault configuration."""


def query_documents(vault_root: str | Path, query: Mapping[str, object]) -> list[dict]:
    """Return Markdown documents selected by a JSON-compatible query object."""
    if not isinstance(query, Mapping):
        raise QueryError("query must be a JSON object")
    allowed = {
        "path",
        "scope",
        "where",
        "includeBody",
        "includeAbsolutePath",
        "projection",
        "sort",
        "skip",
        "limit",
        "bodyContains",
    }
    unknown = set(query) - allowed
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
    body_contains = query.get("bodyContains")
    if body_contains is not None and not isinstance(body_contains, str):
        raise QueryError("bodyContains must be a string")
    skip = _non_negative_int(query.get("skip", 0), "skip")
    limit = query.get("limit")
    if limit is not None:
        limit = _non_negative_int(limit, "limit")
    projection = _validate_projection(query.get("projection"))
    sort = _validate_sort(query.get("sort"))
    where = query.get("where", {})
    if "where" in query and (not isinstance(where, Mapping) or not where):
        raise QueryError("where must be a non-empty object")
    try:
        validate_filter(where)
    except FilterError as error:
        raise QueryError(str(error)) from error

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
                include_body=query.get("includeBody", False)
                or body_contains is not None,
                include_absolute_path=query.get("includeAbsolutePath", False),
            )
            document["params"] = captures
            if not _matches_where(document, relative, captures, where):
                continue
            if body_contains is not None and body_contains not in document.get(
                "body", ""
            ):
                continue
            documents.append(document)
    documents.sort(key=lambda document: document["path"])
    for field, direction in reversed(sort):
        documents.sort(
            key=lambda document: _sort_key(_query_value(document, field)),
            reverse=direction == -1,
        )
    documents = documents[skip:]
    if limit is not None:
        documents = documents[:limit]
    if projection is not None:
        documents = [_project(document, projection) for document in documents]
    if body_contains is not None and not query.get("includeBody", False):
        for document in documents:
            document.pop("body", None)
    return documents


def _non_negative_int(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise QueryError(f"{name} must be a non-negative integer")
    return value


def _validate_projection(value):
    if value is None:
        return None
    if not isinstance(value, Mapping) or not value:
        raise QueryError("projection must be a non-empty object")
    modes = set()
    for field, enabled in value.items():
        _validate_query_field(field, "projection")
        if (
            isinstance(enabled, bool)
            or not isinstance(enabled, int)
            or enabled not in (0, 1)
        ):
            raise QueryError("projection values must be 1 or 0")
        if field != "$path":
            modes.add(bool(enabled))
    if len(modes) > 1:
        raise QueryError("projection cannot mix inclusion and exclusion")
    return {field: bool(enabled) for field, enabled in value.items()}


def _validate_sort(value):
    if value is None:
        return []
    if not isinstance(value, Mapping) or not value:
        raise QueryError("sort must be a non-empty object")
    result = []
    for field, direction in value.items():
        _validate_query_field(field, "sort")
        if isinstance(direction, bool) or direction not in (1, -1):
            raise QueryError("sort directions must be 1 or -1")
        result.append((field, direction))
    return result


def _validate_query_field(field, option):
    if (
        not isinstance(field, str)
        or not field
        or any(not part for part in field.split("."))
    ):
        raise QueryError(f"{option} field names must be non-empty dotted paths")
    if (
        field.startswith("$")
        and field not in {"$path", "$directory", "$filename"}
        and not field.startswith("$params.")
    ):
        raise QueryError(f"unsupported {option} system field: {field}")
    if field == "$params.":
        raise QueryError(f"{option} $params field requires a capture name")


_MISSING = object()


def _query_value(document, field):
    if field == "$path":
        return document["path"]
    if field == "$directory":
        return document["path"].rsplit("/", 1)[0] if "/" in document["path"] else ""
    if field == "$filename":
        return document["path"].rsplit("/", 1)[-1]
    if field.startswith("$params."):
        value = document.get("params", {})
        field = field[len("$params.") :]
    else:
        value = document.get("frontmatter", {})
    for segment in field.split("."):
        if not isinstance(value, Mapping) or segment not in value:
            return _MISSING
        value = value[segment]
    return value


def _sort_key(value):
    # A fixed type rank keeps mixed and missing values comparable.
    if value is _MISSING:
        return (0, "")
    if value is None:
        return (1, "")
    if isinstance(value, bool):
        return (2, int(value))
    if isinstance(value, (int, float)):
        number = float(value)
        return (3, number if math.isfinite(number) else 0)
    if isinstance(value, str):
        return (4, value)
    if isinstance(value, list):
        return (5, json.dumps(value, sort_keys=True, ensure_ascii=False))
    return (6, json.dumps(value, sort_keys=True, ensure_ascii=False))


def _project(document, projection):
    included = any(projection.values())
    result = {
        "path": document["path"],
        "params": {},
        "frontmatter": {},
        "diagnostics": document["diagnostics"],
    }
    for key in ("absolutePath", "body"):
        if key in document:
            result[key] = document[key]
    if included:
        for field, enabled in projection.items():
            if not enabled:
                continue
            if field == "$path":
                continue
            if field.startswith("$params."):
                _copy_projected(result["params"], document["params"], field[8:])
            elif field in ("$directory", "$filename"):
                result[field] = _query_value(document, field)
            elif not field.startswith("$"):
                _copy_projected(result["frontmatter"], document["frontmatter"], field)
    else:
        result["frontmatter"] = dict(document["frontmatter"])
        result["params"] = dict(document["params"])
        for field, enabled in projection.items():
            if enabled:
                continue
            if field.startswith("$params."):
                _remove_projected(result["params"], field[8:])
                continue
            if field.startswith("$"):
                continue
            _remove_projected(result["frontmatter"], field)
    return result


def _copy_projected(target, source, dotted):
    parts = dotted.split(".")
    value = source
    for part in parts:
        if not isinstance(value, Mapping) or part not in value:
            return
        value = value[part]
    cursor = target
    for part in parts[:-1]:
        cursor = cursor.setdefault(part, {})
    cursor[parts[-1]] = value


def _remove_projected(target, dotted):
    parts = dotted.split(".")
    cursor = target
    for part in parts[:-1]:
        if not isinstance(cursor, dict) or part not in cursor:
            return
        cursor = cursor[part]
    if isinstance(cursor, dict):
        cursor.pop(parts[-1], None)


def _matches_where(document, path, captures, where):
    if not where:
        return True
    if document["diagnostics"] and not uses_only_system_fields(where):
        return False
    segments = path.split("/")
    location = {
        "$path": path,
        "$directory": "/".join(segments[:-1]),
        "$filename": segments[-1],
        **{f"$params.{name}": value for name, value in captures.items()},
    }
    return matches({**document["frontmatter"], **location}, where)
