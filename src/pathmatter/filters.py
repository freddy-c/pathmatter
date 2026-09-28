"""MongoDB-inspired predicates for parsed document values."""

import math
from collections.abc import Mapping


class FilterError(ValueError):
    """A filter uses unsupported syntax."""


_FIELD_OPS = {
    "$eq",
    "$ne",
    "$gt",
    "$gte",
    "$lt",
    "$lte",
    "$in",
    "$nin",
    "$exists",
    "$not",
    "$elemMatch",
}
_COMPARISONS = {"$gt", "$gte", "$lt", "$lte"}
_SYSTEM_FIELDS = {"$path", "$directory", "$filename"}
_MISSING = object()


def _is_operator_object(value):
    return isinstance(value, Mapping) and any(str(key).startswith("$") for key in value)


def _validate_field_ops(ops):
    for op, value in ops.items():
        if op not in _FIELD_OPS:
            raise FilterError(f"unsupported filter operator: {op}")
        if op in ("$in", "$nin") and not isinstance(value, list):
            raise FilterError(f"{op} requires an array")
        if op == "$exists" and not isinstance(value, bool):
            raise FilterError("$exists requires a boolean")
        if op in _COMPARISONS and isinstance(value, (Mapping, list)):
            raise FilterError(f"{op} requires a scalar value")
        if op == "$not" and not _is_operator_object(value):
            raise FilterError("field $not requires an operator object")
        if op == "$elemMatch":
            validate_filter(value, element=True)


def validate_filter(condition, *, element=False):
    """Reject unsupported operators and malformed logical clauses."""
    if not isinstance(condition, Mapping):
        raise FilterError("where conditions must be objects")
    if element and condition and all(str(key).startswith("$") for key in condition):
        _validate_field_ops(condition)
        return
    for key, value in condition.items():
        if not isinstance(key, str):
            raise FilterError("filter field names must be strings")
        if key in ("$and", "$or"):
            if not isinstance(value, list):
                raise FilterError(f"{key} requires an array of filter objects")
            for clause in value:
                validate_filter(clause, element=element)
        elif key == "$not":
            validate_filter(value, element=element)
        elif (
            key.startswith("$")
            and key not in _SYSTEM_FIELDS
            and not key.startswith("$params.")
        ):
            raise FilterError(f"unsupported filter operator: {key}")
        elif not key:
            raise FilterError("filter field names must not be empty")
        elif _is_operator_object(value):
            _validate_field_ops(value)


def _lookup(document, path):
    if path.startswith("$params."):
        return document.get(path, _MISSING)
    value = document
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return _MISSING
        value = value[part]
    return value


def _equal(actual, expected):
    if isinstance(actual, list):
        return any(_equal(item, expected) for item in actual)
    if isinstance(actual, bool) != isinstance(expected, bool):
        return False
    same_type = type(actual) is type(expected)
    both_numbers = all(
        isinstance(v, (int, float)) and not isinstance(v, bool)
        for v in (actual, expected)
    )
    return (same_type or both_numbers) and actual == expected


def _compare(actual, expected, op):
    if isinstance(actual, bool) or isinstance(expected, bool):
        return False
    numbers = isinstance(actual, (int, float)) and isinstance(expected, (int, float))
    strings = isinstance(actual, str) and isinstance(expected, str)
    if not (numbers or strings):
        return False
    if numbers and not (math.isfinite(actual) and math.isfinite(expected)):
        return False
    return {
        "$gt": actual > expected,
        "$gte": actual >= expected,
        "$lt": actual < expected,
        "$lte": actual <= expected,
    }[op]


def _operator_matches(actual, op, expected):
    if op == "$exists":
        return (actual is not _MISSING) is expected
    if op == "$eq":
        return actual is not _MISSING and _equal(actual, expected)
    if op == "$ne":
        return actual is _MISSING or not _equal(actual, expected)
    if op == "$in":
        return actual is not _MISSING and any(_equal(actual, item) for item in expected)
    if op == "$nin":
        return actual is _MISSING or not any(_equal(actual, item) for item in expected)
    if op in _COMPARISONS:
        return actual is not _MISSING and _compare(actual, expected, op)
    if op == "$not":
        return not _field_matches(actual, expected)
    if op == "$elemMatch":
        return isinstance(actual, list) and any(
            _element_matches(item, expected) for item in actual
        )
    return False


def _field_matches(actual, condition):
    if not _is_operator_object(condition):
        return actual is not _MISSING and _equal(actual, condition)
    return all(_operator_matches(actual, op, value) for op, value in condition.items())


def _element_matches(item, condition):
    if _is_operator_object(condition):
        return _field_matches(item, condition)
    return isinstance(item, Mapping) and matches(item, condition)


def matches(document, condition):
    """Evaluate a previously validated filter against a document mapping."""
    for key, expected in condition.items():
        if key == "$and":
            matched = all(matches(document, clause) for clause in expected)
        elif key == "$or":
            matched = any(matches(document, clause) for clause in expected)
        elif key == "$not":
            matched = not matches(document, expected)
        else:
            matched = _field_matches(_lookup(document, key), expected)
        if not matched:
            return False
    return True


def uses_only_system_fields(condition):
    """Return whether a filter refers only to derived location fields."""
    for key, value in condition.items():
        if key in ("$and", "$or"):
            if not all(uses_only_system_fields(clause) for clause in value):
                return False
        elif key == "$not":
            if not uses_only_system_fields(value):
                return False
        elif key not in _SYSTEM_FIELDS and not key.startswith("$params."):
            return False
    return True
