"""Translate a raw MongoDB filter into DbGate's condition AST.

DbGate's ``collection-data`` endpoint does not accept a Mongo filter document:
it wants its own condition tree (``{"conditionType": "binary", ...}``) and
rejects anything else with ``Unknown condition type undefined``. Agents write
Mongo filters naturally, so the translation happens here — a pure function with
no I/O, covering the operators the AST can express.
"""

from __future__ import annotations

from typing import Any

_COMPARISON = {
    "$eq": "=",
    "$ne": "!=",
    "$gt": ">",
    "$gte": ">=",
    "$lt": "<",
    "$lte": "<=",
}
_LOGICAL = {"$and": "and", "$or": "or"}


def _column(name: str) -> dict:
    return {"exprType": "column", "columnName": name}


def _value(value: Any) -> dict:
    return {"exprType": "value", "value": value}


def _binary(field: str, operator: str, value: Any) -> dict:
    return {
        "conditionType": "binary",
        "operator": operator,
        "left": _column(field),
        "right": _value(value),
    }


def _combine(conditions: list[dict], kind: str = "and") -> dict | None:
    """Fold a list of conditions: none → None, one → itself, many → and/or."""
    if not conditions:
        return None
    if len(conditions) == 1:
        return conditions[0]
    return {"conditionType": kind, "conditions": conditions}


def _field_conditions(field: str, spec: Any) -> list[dict]:
    """Conditions for one field: a bare value, or a dict of Mongo operators."""
    if not isinstance(spec, dict):
        return [_binary(field, "=", spec)]
    out = []
    for operator, operand in spec.items():
        if operator in _COMPARISON:
            out.append(_binary(field, _COMPARISON[operator], operand))
        elif operator == "$regex":
            out.append(
                {
                    "conditionType": "like",
                    "left": _column(field),
                    "right": _value(f"%{operand}%"),
                }
            )
        elif operator == "$in":
            if not isinstance(operand, list):
                raise ValueError("$in expects a list of values")
            out.append(
                {"conditionType": "in", "expr": _column(field), "values": operand}
            )
        else:
            raise ValueError(f"unsupported filter operator '{operator}'")
    return out


def to_condition(filter: dict | None) -> dict | None:
    """Translate a Mongo filter document into a DbGate condition, or None."""
    if not filter:
        return None
    conditions: list[dict] = []
    for key, spec in filter.items():
        if key in _LOGICAL:
            if not isinstance(spec, list):
                raise ValueError(f"{key} expects a list of filters")
            branches = [c for c in (to_condition(f) for f in spec) if c is not None]
            combined = _combine(branches, _LOGICAL[key])
            if combined is not None:
                conditions.append(combined)
        elif key.startswith("$"):
            raise ValueError(f"unsupported filter operator '{key}'")
        else:
            conditions.extend(_field_conditions(key, spec))
    return _combine(conditions)


def to_sort(sort: dict | None) -> list[dict] | None:
    """Translate ``{"field": 1 | -1}`` into DbGate's sort list."""
    if not sort:
        return None
    return [
        {"columnName": field, "direction": "ASC" if direction >= 0 else "DESC"}
        for field, direction in sort.items()
    ]
