"""Read ordinary Markdown documents from a vault."""

import math
import re
from datetime import date, datetime
from pathlib import Path

import yaml

_BOUNDARY = re.compile(r"^---[ \t]*$")


def _json_value(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return {key: _json_value(item) for key, item in value.items()}
    raise ValueError("frontmatter must contain JSON-compatible values and string keys")


def read_document(
    path: Path,
    relative_path: str,
    *,
    include_body: bool,
    include_absolute_path: bool,
    source: str | None = None,
) -> dict:
    """Read a document; parsing failures are returned as diagnostics."""
    result: dict = {
        "path": relative_path,
        "params": {},
        "frontmatter": {},
        "diagnostics": [],
    }
    if include_absolute_path:
        result["absolutePath"] = str(path.absolute())
    if source is None:
        try:
            source = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as error:
            result["diagnostics"].append({"code": "read_error", "message": str(error)})
            if include_body:
                result["body"] = ""
            return result

    lines = source.splitlines(keepends=True)
    body = source
    if lines and _BOUNDARY.fullmatch(lines[0].rstrip("\r\n")):
        closing = next(
            (
                index
                for index in range(1, len(lines))
                if _BOUNDARY.fullmatch(lines[index].rstrip("\r\n"))
            ),
            None,
        )
        if closing is None:
            result["diagnostics"].append(
                {
                    "code": "frontmatter_parse_error",
                    "message": "missing closing --- delimiter",
                }
            )
        else:
            body = "".join(lines[closing + 1 :])
            try:
                parsed = yaml.safe_load("".join(lines[1:closing]))
                if parsed is not None:
                    if not isinstance(parsed, dict):
                        raise ValueError("frontmatter must be a mapping")
                    result["frontmatter"] = _json_value(parsed)
            except (yaml.YAMLError, ValueError) as error:
                result["diagnostics"].append(
                    {"code": "frontmatter_parse_error", "message": str(error)}
                )
    if include_body:
        result["body"] = body
    return result
