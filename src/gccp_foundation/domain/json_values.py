"""Strict JSON, immutable payloads and exact decimal-string canonicalization."""
from collections.abc import Mapping
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any

from .errors import ContractError

DECIMAL_PATTERN = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z", re.ASCII)


def decimal_text(value: str) -> str:
    if not isinstance(value, str) or not DECIMAL_PATTERN.fullmatch(value):
        raise ContractError("Expected a nonnegative exact decimal string")
    if "." in value:
        value = value.rstrip("0").rstrip(".")
    return value


def freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        if any(not isinstance(k, str) for k in value):
            raise ContractError("JSON object keys must be strings")
        return MappingProxyType({k: freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(v) for v in value)
    if value is None or type(value) in (str, int, bool):
        return value
    raise ContractError("Non-JSON value or binary float is not permitted")


def thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: thaw(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [thaw(v) for v in value]
    return value


def loads(text: str) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ContractError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ContractError(f"Invalid JSON numeric constant: {value}")

    try:
        value = json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        if isinstance(exc, ContractError):
            raise
        raise ContractError(str(exc)) from exc
    return thaw(freeze(value))


def digest(value: Any) -> str:
    raw = json.dumps(thaw(freeze(value)), ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
