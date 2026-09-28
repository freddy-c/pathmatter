"""Vault-relative document path patterns."""

import re


class PatternError(ValueError):
    """A path pattern does not follow the supported grammar."""


_CAPTURE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}\Z")


def _segments(value: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, str) or (not value and not allow_empty):
        raise PatternError("path must be a non-empty string")
    if not value:
        return []
    parts = value.split("/")
    if value.startswith("/") or any(part in ("", ".", "..") for part in parts):
        raise PatternError(
            "path must be vault-relative without empty, . or .. segments"
        )
    if "\\" in value:
        raise PatternError("path must use / separators")
    return parts


def validate_relative_directory(value: str) -> str:
    """Validate a vault-relative directory, allowing the root as an empty string."""
    _segments(value, allow_empty=True)
    return value


def validate_document_path(value: str) -> str:
    """Validate a vault-relative Markdown document path."""
    parts = _segments(value)
    if not parts[-1].endswith(".md"):
        raise PatternError("document path must end in .md")
    if "\x00" in value:
        raise PatternError("document path must not contain NUL")
    if ":" in value:
        raise PatternError("document path must not contain a drive separator")
    return value


class PathPattern:
    """A whole-path matcher with named, single-segment captures."""

    def __init__(self, pattern: str):
        parts = _segments(pattern)
        if parts[-1] == "**":
            raise PatternError(
                "** matches directories and must be followed by a filename segment"
            )
        names: set[str] = set()
        compiled: list[tuple[str, object]] = []
        for part in parts:
            if part == "**":
                compiled.append(("globstar", None))
            elif "**" in part:
                raise PatternError("** must occupy a complete path segment")
            elif match := _CAPTURE.fullmatch(part):
                name = match.group(1)
                if name in names:
                    raise PatternError(f"duplicate capture name: {name}")
                names.add(name)
                compiled.append(("capture", name))
            elif "{" in part or "}" in part:
                raise PatternError(f"malformed capture segment: {part}")
            else:
                expression = "".join(
                    ".*" if char == "*" else re.escape(char) for char in part
                )
                compiled.append(("segment", re.compile(expression + r"\Z")))
        self.pattern = pattern
        self._parts = compiled

    def match(self, path: str) -> dict[str, str] | None:
        """Return captured values, or None if the whole path does not match."""
        parts = _segments(path)

        def visit(
            pattern_index: int, path_index: int, captures: dict[str, str]
        ) -> dict[str, str] | None:
            if pattern_index == len(self._parts):
                return captures if path_index == len(parts) else None
            kind, value = self._parts[pattern_index]
            if kind == "globstar":
                for next_index in range(path_index, len(parts) + 1):
                    result = visit(pattern_index + 1, next_index, captures)
                    if result is not None:
                        return result
                return None
            if path_index == len(parts):
                return None
            if kind == "capture":
                return visit(
                    pattern_index + 1,
                    path_index + 1,
                    {**captures, str(value): parts[path_index]},
                )
            if value.fullmatch(parts[path_index]):
                return visit(pattern_index + 1, path_index + 1, captures)
            return None

        return visit(0, 0, {})
