"""Checked narrowing at the parse boundary.

`json.loads` and `yaml.safe_load` both return `Any`, and this package forbids
explicit `Any`. Narrowing here is not type appeasement: the store is append-only
and the findings document is hand-authored, so malformed input should produce a
refusal that names the file and field it gave up on, rather than a `KeyError` or
a `TypeError` several frames deeper.

Each helper takes the exception type to raise, so the store and the findings
reader report their own named refusals over one implementation.
"""

from __future__ import annotations

from comparative_judgment.core.errors import ComparativeJudgmentError

ErrorType = type[ComparativeJudgmentError]


def as_dict(value: object, where: str, *, error: ErrorType) -> dict[str, object]:
    if not isinstance(value, dict):
        msg = f"{where}: expected an object, found {type(value).__name__}"
        raise error(msg)
    return {str(k): v for k, v in value.items()}


def as_list(value: object, where: str, *, error: ErrorType) -> list[object]:
    if not isinstance(value, list):
        msg = f"{where}: expected a list, found {type(value).__name__}"
        raise error(msg)
    return list(value)


def as_str(value: object, where: str, *, error: ErrorType) -> str:
    if not isinstance(value, str):
        msg = f"{where}: expected a string, found {type(value).__name__}"
        raise error(msg)
    return value


def as_int(value: object, where: str, *, error: ErrorType) -> int:
    # bool is an int subclass; a boolean where a sequence number belongs is
    # corrupt data, not a number.
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"{where}: expected an integer, found {type(value).__name__}"
        raise error(msg)
    return value


def field(obj: dict[str, object], key: str, where: str, *, error: ErrorType) -> object:
    if key not in obj:
        msg = f"{where}: missing field {key!r}"
        raise error(msg)
    return obj[key]
