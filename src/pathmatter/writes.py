"""Preview and write validated Markdown documents in a vault."""

import copy
import os
import stat
import tempfile
from collections.abc import Mapping
from pathlib import Path

import yaml

from .documents import _json_value, read_document
from .patterns import PatternError, validate_document_path
from .rules import RuleConfigError, load_rules


class WriteError(ValueError):
    """A write request or its vault configuration is invalid."""


class WriteValidationError(WriteError):
    """A valid request produced a document that cannot be written."""

    def __init__(self, preview: dict):
        self.preview = preview
        super().__init__("document cannot be written")


def _vault_root(vault_root: str | Path) -> Path:
    root = Path(vault_root)
    if root.is_symlink() or not root.is_dir():
        raise WriteError("vault root must be an existing, non-symlink directory")
    return root.absolute()


def _target_path(root: Path, document_path: object) -> Path:
    try:
        validate_document_path(document_path)
    except PatternError as error:
        raise WriteError(str(error)) from error
    current = root
    parts = document_path.split("/")
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise WriteError(f"document path contains a symlink: {current}")
    parent = root
    for part in parts[:-1]:
        parent = parent / part
        if parent.exists() and not parent.is_dir():
            raise WriteError(f"document parent is not a directory: {parent}")
    return current


def _frontmatter(value: object) -> dict:
    if not isinstance(value, Mapping):
        raise WriteError("frontmatter must be an object")
    try:
        normalized = _json_value(dict(value))
    except ValueError as error:
        raise WriteError(str(error)) from error
    return normalized


def _source(frontmatter: dict, body: str) -> str:
    serialized = yaml.safe_dump(
        frontmatter, allow_unicode=True, sort_keys=False, default_flow_style=False
    )
    return f"---\n{serialized}---\n{body}"


def _schema_diagnostics(root: Path, document: dict) -> list[dict]:
    try:
        return load_rules(root).diagnostics(document, root / document["path"])
    except RuleConfigError as error:
        raise WriteError(str(error)) from error


def _preview_create(root: Path, request: Mapping) -> dict:
    if set(request) - {"operation", "path", "frontmatter", "body"} or not {
        "operation", "path", "frontmatter"
    } <= set(request):
        raise WriteError("create request requires operation, path, and frontmatter")
    path = request["path"]
    target = _target_path(root, path)
    frontmatter = _frontmatter(request["frontmatter"])
    body = request.get("body", "")
    if not isinstance(body, str):
        raise WriteError("body must be a string")
    document = {
        "operation": "create",
        "path": path,
        "absolutePath": str(target),
        "frontmatter": frontmatter,
        "body": body,
        "diagnostics": [],
    }
    document["diagnostics"].extend(_schema_diagnostics(root, document))
    if target.exists():
        document["diagnostics"].append(
            {
                "code": "occupied_target",
                "message": "document path is already occupied",
                "path": path,
                "absolutePath": str(target),
            }
        )
    return document


def _field_path(field: object) -> list[str]:
    if (
        not isinstance(field, str)
        or not field
        or field.startswith("$")
        or any(not segment or segment.startswith("$") for segment in field.split("."))
    ):
        raise WriteError("update fields must be non-empty dotted frontmatter paths")
    return field.split(".")


def _apply_patch(frontmatter: dict, patch: object) -> dict:
    if not isinstance(patch, Mapping) or set(patch) - {"$set", "$unset"}:
        raise WriteError("update must contain only $set and $unset objects")
    setting = patch.get("$set", {})
    unsetting = patch.get("$unset", {})
    if not isinstance(setting, Mapping) or not isinstance(unsetting, Mapping):
        raise WriteError("$set and $unset must be objects")
    paths = [_field_path(field) for field in (*setting, *unsetting)]
    for index, left in enumerate(paths):
        for right in paths[index + 1 :]:
            if left == right or left == right[: len(left)] or right == left[: len(right)]:
                raise WriteError("update fields must not overlap")
    result = copy.deepcopy(frontmatter)
    for field, value in setting.items():
        try:
            value = _json_value(value)
        except ValueError as error:
            raise WriteError(str(error)) from error
        parts = _field_path(field)
        cursor = result
        for part in parts[:-1]:
            if part not in cursor:
                cursor[part] = {}
            if not isinstance(cursor[part], dict):
                raise WriteError(f"cannot traverse non-object frontmatter field: {part}")
            cursor = cursor[part]
        cursor[parts[-1]] = value
    for field in unsetting:
        parts = _field_path(field)
        cursor = result
        for part in parts[:-1]:
            if part not in cursor:
                break
            if not isinstance(cursor[part], dict):
                raise WriteError(f"cannot traverse non-object frontmatter field: {part}")
            cursor = cursor[part]
        else:
            cursor.pop(parts[-1], None)
    return result


def _prepare_update(root: Path, request: Mapping) -> tuple[dict, bytes]:
    if set(request) - {"operation", "path", "update", "body"} or not {
        "operation", "path"
    } <= set(request):
        raise WriteError("update request requires operation and path")
    if "update" not in request and "body" not in request:
        raise WriteError("update request needs an update patch or body")
    path = request["path"]
    target = _target_path(root, path)
    if not target.is_file():
        raise WriteError(f"document does not exist: {target}")
    try:
        original = target.read_bytes()
        source = original.decode("utf-8-sig")
    except (OSError, UnicodeError) as error:
        raise WriteError(f"cannot read document {target}: {error}") from error
    existing = read_document(
        target, path, include_body=True, include_absolute_path=True, source=source
    )
    if existing["diagnostics"]:
        raise WriteError(f"cannot update document with invalid frontmatter: {target}")
    frontmatter = (
        _apply_patch(existing["frontmatter"], request["update"])
        if "update" in request
        else existing["frontmatter"]
    )
    if "body" not in request and frontmatter == existing["frontmatter"]:
        raise WriteError("update must change frontmatter or supply body")
    body = request.get("body", existing["body"])
    if not isinstance(body, str):
        raise WriteError("body must be a string")
    document = {
        "operation": "update",
        "path": path,
        "absolutePath": str(target),
        "frontmatter": frontmatter,
        "body": body,
        "diagnostics": [],
    }
    document["diagnostics"].extend(_schema_diagnostics(root, document))
    return document, original


def preview_document(vault_root: str | Path, request: Mapping) -> dict:
    """Return proposed content and diagnostics without changing the vault."""
    if not isinstance(request, Mapping):
        raise WriteError("request must be a JSON object")
    root = _vault_root(vault_root)
    operation = request.get("operation")
    if operation == "create":
        return _preview_create(root, request)
    if operation == "update":
        return _prepare_update(root, request)[0]
    raise WriteError("operation must be create or update")


def create_document(vault_root: str | Path, request: Mapping) -> dict:
    """Create a validated document without replacing an existing path."""
    if not isinstance(request, Mapping):
        raise WriteError("request must be a JSON object")
    root = _vault_root(vault_root)
    preview = _preview_create(root, {**request, "operation": "create"})
    if preview["diagnostics"]:
        raise WriteValidationError(preview)
    target = _target_path(root, preview["path"])
    parent = root
    try:
        for part in preview["path"].split("/")[:-1]:
            parent = parent / part
            if not parent.exists():
                parent.mkdir()
            if parent.is_symlink() or not parent.is_dir():
                raise WriteError(f"document parent is not an ordinary directory: {parent}")
    except OSError as error:
        raise WriteError(f"cannot create document parent {parent}: {error}") from error
    _target_path(root, preview["path"])
    try:
        with target.open("x", encoding="utf-8", newline="") as output:
            output.write(_source(preview["frontmatter"], preview["body"]))
    except FileExistsError as error:
        preview["diagnostics"].append(
            {
                "code": "occupied_target",
                "message": "document path is already occupied",
                "path": preview["path"],
                "absolutePath": str(target),
            }
        )
        raise WriteValidationError(preview) from error
    except OSError as error:
        raise WriteError(f"cannot create document {target}: {error}") from error
    return preview


def update_document(vault_root: str | Path, request: Mapping) -> dict:
    """Patch the latest readable document and replace it after validation."""
    if not isinstance(request, Mapping):
        raise WriteError("request must be a JSON object")
    root = _vault_root(vault_root)
    preview, original = _prepare_update(root, {**request, "operation": "update"})
    if preview["diagnostics"]:
        raise WriteValidationError(preview)
    target = _target_path(root, preview["path"])
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=target.parent, delete=False
        ) as output:
            temporary = Path(output.name)
            output.write(_source(preview["frontmatter"], preview["body"]))
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, stat.S_IMODE(target.stat().st_mode))
        if target.read_bytes() != original:
            raise WriteError(f"document changed during update; retry: {target}")
        _target_path(root, preview["path"])
        os.replace(temporary, target)
    except OSError as error:
        raise WriteError(f"cannot update document {target}: {error}") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return preview
