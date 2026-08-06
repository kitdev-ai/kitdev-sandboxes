"""Dependency-free assertions for the JSON Schema subset used by test contracts."""

from __future__ import annotations

import re
from collections.abc import Mapping


def _matches_type(value: object, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return type(value) is int
    if expected == "number":
        return type(value) in {int, float}
    if expected == "boolean":
        return type(value) is bool
    if expected == "null":
        return value is None
    raise AssertionError(f"unsupported schema type: {expected}")


def _json_equal(left: object, right: object) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    return left == right


def assert_schema_conforms(
    instance: object,
    schema: Mapping[str, object],
    *,
    path: str = "$",
) -> None:
    """Assert conformance for the closed, reference-free schemas in this repo."""

    declared_type = schema.get("type")
    if declared_type is not None:
        expected_types = (
            declared_type if isinstance(declared_type, list) else [declared_type]
        )
        if not all(isinstance(expected, str) for expected in expected_types):
            raise AssertionError(f"{path}: schema type must be a string or string list")
        if not any(_matches_type(instance, expected) for expected in expected_types):
            raise AssertionError(f"{path}: value does not match type {declared_type!r}")

    if "const" in schema and not _json_equal(instance, schema["const"]):
        raise AssertionError(f"{path}: value does not match const")

    enum = schema.get("enum")
    if enum is not None:
        if not isinstance(enum, list):
            raise AssertionError(f"{path}: schema enum must be an array")
        if not any(_json_equal(instance, member) for member in enum):
            raise AssertionError(f"{path}: value is not in enum")

    if isinstance(instance, dict):
        required = schema.get("required", [])
        if not isinstance(required, list) or not all(
            isinstance(key, str) for key in required
        ):
            raise AssertionError(f"{path}: schema required must be a string array")
        missing = [key for key in required if key not in instance]
        if missing:
            raise AssertionError(f"{path}: missing required properties {missing!r}")

        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise AssertionError(f"{path}: schema properties must be an object")
        extras = instance.keys() - properties.keys()
        additional = schema.get("additionalProperties", True)
        if extras and additional is False:
            raise AssertionError(f"{path}: additional properties {sorted(extras)!r}")

        for key, value in instance.items():
            child_schema = properties.get(key)
            if child_schema is not None:
                if not isinstance(child_schema, dict):
                    raise AssertionError(f"{path}.{key}: property schema must be an object")
                assert_schema_conforms(value, child_schema, path=f"{path}.{key}")
            elif isinstance(additional, dict):
                assert_schema_conforms(value, additional, path=f"{path}.{key}")

    if isinstance(instance, list):
        minimum_items = schema.get("minItems")
        maximum_items = schema.get("maxItems")
        if minimum_items is not None and len(instance) < minimum_items:
            raise AssertionError(f"{path}: array is shorter than minItems")
        if maximum_items is not None and len(instance) > maximum_items:
            raise AssertionError(f"{path}: array is longer than maxItems")
        item_schema = schema.get("items")
        if item_schema is not None:
            if not isinstance(item_schema, dict):
                raise AssertionError(f"{path}: items schema must be an object")
            for index, value in enumerate(instance):
                assert_schema_conforms(value, item_schema, path=f"{path}[{index}]")

    if type(instance) in {int, float}:
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if minimum is not None and instance < minimum:
            raise AssertionError(f"{path}: number is below minimum")
        if maximum is not None and instance > maximum:
            raise AssertionError(f"{path}: number is above maximum")

    if isinstance(instance, str):
        minimum_length = schema.get("minLength")
        maximum_length = schema.get("maxLength")
        if minimum_length is not None and len(instance) < minimum_length:
            raise AssertionError(f"{path}: string is shorter than minLength")
        if maximum_length is not None and len(instance) > maximum_length:
            raise AssertionError(f"{path}: string is longer than maxLength")
        pattern = schema.get("pattern")
        if pattern is not None:
            if not isinstance(pattern, str):
                raise AssertionError(f"{path}: pattern must be a string")
            if re.search(pattern, instance) is None:
                raise AssertionError(f"{path}: string does not match pattern")
