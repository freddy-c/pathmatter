"""Path-based frontmatter rules for a Markdown vault."""

import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from .patterns import PathPattern, PatternError, validate_relative_directory


class RuleConfigError(ValueError):
    """A vault rule or its referenced schema is invalid."""


@dataclass(frozen=True)
class Rule:
    match: str
    pattern: PathPattern
    entity: str | None
    template: str | None
    schema_path: str | None
    validator: Draft202012Validator | None


class RuleSet:
    def __init__(self, rules: list[Rule]):
        self.rules = rules

    def matching(self, document_path: str) -> list[tuple[Rule, dict[str, str]]]:
        matches = []
        for rule in self.rules:
            captures = rule.pattern.match(document_path)
            if captures is not None:
                matches.append((rule, captures))
        for attribute in ("entity", "template"):
            choices = {getattr(rule, attribute) for rule, _ in matches if getattr(rule, attribute) is not None}
            if len(choices) > 1:
                raise RuleConfigError(
                    f"conflicting {attribute} values for {document_path}: "
                    + ", ".join(sorted(choices))
                )
        return matches

    def diagnostics(self, document: dict, absolute_path: Path) -> list[dict]:
        """Return every schema violation, retaining the document's parse errors."""
        matches = self.matching(document["path"])
        if document["diagnostics"]:
            return []
        diagnostics = []
        for rule, _ in matches:
            if rule.validator is None:
                continue
            errors = sorted(
                rule.validator.iter_errors(document["frontmatter"]),
                key=lambda error: (list(map(str, error.absolute_path)), error.message),
            )
            for error in errors:
                field_parts = [str(part) for part in error.absolute_path]
                if error.validator == "required":
                    missing = next(
                        (name for name in error.validator_value if name not in error.instance),
                        None,
                    )
                    if missing is not None:
                        field_parts.append(missing)
                diagnostics.append(
                    {
                        "code": "schema_validation_error",
                        "message": error.message,
                        "path": document["path"],
                        "absolutePath": str(absolute_path),
                        "rule": rule.match,
                        "schema": rule.schema_path,
                        "field": ".".join(field_parts),
                    }
                )
        return diagnostics


def load_rules(vault_root: Path) -> RuleSet:
    """Load .pathmatter.yaml and schemas relative to the vault root."""
    config = vault_root / ".pathmatter.yaml"
    if not config.exists() and not config.is_symlink():
        return RuleSet([])
    if config.is_symlink() or not config.is_file():
        raise RuleConfigError(f"rule config must be a regular file: {config}")
    try:
        data = yaml.safe_load(config.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise RuleConfigError(f"cannot read rule config {config}: {error}") from error
    if not isinstance(data, dict) or set(data) != {"rules"} or not isinstance(data["rules"], list):
        raise RuleConfigError(f"{config}: expected a mapping with a rules list")
    rules = []
    for number, raw in enumerate(data["rules"], start=1):
        label = f"{config}: rule {number}"
        if not isinstance(raw, dict) or set(raw) - {"match", "entity", "schema", "template"}:
            raise RuleConfigError(f"{label}: expected match, entity, schema, or template fields")
        match = raw.get("match")
        try:
            pattern = PathPattern(match)
        except PatternError as error:
            raise RuleConfigError(f"{label}: {error}") from error
        for field in ("entity", "template"):
            if field in raw and (not isinstance(raw[field], str) or not raw[field]):
                raise RuleConfigError(f"{label}: {field} must be a non-empty string")
        schema_reference = raw.get("schema")
        schema_path = None
        validator = None
        if schema_reference is not None:
            if not isinstance(schema_reference, str):
                raise RuleConfigError(f"{label}: schema must be a vault-relative JSON path")
            try:
                validate_relative_directory(schema_reference)
            except PatternError as error:
                raise RuleConfigError(f"{label}: invalid schema path: {error}") from error
            if "/" not in schema_reference and "." not in schema_reference:
                schema_path = f"schemas/{schema_reference}.json"
            elif schema_reference.endswith(".json"):
                schema_path = schema_reference
            else:
                raise RuleConfigError(f"{label}: schema must be a name or .json path")
            schema_file = vault_root
            for part in schema_path.split("/"):
                schema_file = schema_file / part
                if schema_file.is_symlink():
                    raise RuleConfigError(f"{label}: schema path contains a symlink: {schema_file}")
            try:
                schema = json.loads(schema_file.read_text(encoding="utf-8"))
                if not isinstance(schema, dict):
                    raise ValueError("schema must be a JSON object")
                dialect = schema.get("$schema")
                if dialect not in (None, "https://json-schema.org/draft/2020-12/schema"):
                    raise ValueError("schema must use JSON Schema Draft 2020-12")
                _check_references(schema, schema)
                Draft202012Validator.check_schema(schema)
                validator = Draft202012Validator(schema)
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError, SchemaError) as error:
                raise RuleConfigError(f"{label}: invalid schema {schema_file}: {error}") from error
        rules.append(
            Rule(match, pattern, raw.get("entity"), raw.get("template"), schema_path, validator)
        )
    return RuleSet(rules)


def _check_references(value: object, root: object) -> None:
    """Keep schema references local and reject unresolved JSON pointers."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "$dynamicRef":
                raise ValueError("$dynamicRef is not supported")
            if key == "$ref":
                if not isinstance(item, str) or (item != "#" and not item.startswith("#/")):
                    raise ValueError("only local JSON pointer # references are supported")
                target = root
                for token in unquote(item[2:]).split("/") if item != "#" else []:
                    token = token.replace("~1", "/").replace("~0", "~")
                    if isinstance(target, dict) and token in target:
                        target = target[token]
                    elif isinstance(target, list) and token.isdecimal() and int(token) < len(target):
                        target = target[int(token)]
                    else:
                        raise ValueError(f"unresolved local schema reference: {item}")
            _check_references(item, root)
    elif isinstance(value, list):
        for item in value:
            _check_references(item, root)
