"""Unit tests for the Mongo-filter → DbGate-condition translation."""

import pytest

from dbgate_dashboard_mcp.mongo_filter import to_condition, to_sort


def _col(name):
    return {"exprType": "column", "columnName": name}


def _val(value):
    return {"exprType": "value", "value": value}


def test_empty_filter_is_none():
    assert to_condition(None) is None
    assert to_condition({}) is None


def test_equality_becomes_binary():
    assert to_condition({"name": "AC/DC"}) == {
        "conditionType": "binary",
        "operator": "=",
        "left": _col("name"),
        "right": _val("AC/DC"),
    }


@pytest.mark.parametrize(
    "mongo_op,sql_op",
    [("$eq", "="), ("$ne", "!="), ("$gt", ">"), ("$gte", ">="), ("$lt", "<"), ("$lte", "<=")],
)
def test_comparison_operators(mongo_op, sql_op):
    assert to_condition({"n": {mongo_op: 5}}) == {
        "conditionType": "binary",
        "operator": sql_op,
        "left": _col("n"),
        "right": _val(5),
    }


def test_regex_becomes_like():
    assert to_condition({"title": {"$regex": "Rock"}}) == {
        "conditionType": "like",
        "left": _col("title"),
        "right": _val("%Rock%"),
    }


def test_in_becomes_in_condition():
    assert to_condition({"_id": {"$in": [1, 2]}}) == {
        "conditionType": "in",
        "expr": _col("_id"),
        "values": [1, 2],
    }


def test_multiple_fields_are_anded():
    out = to_condition({"a": 1, "b": 2})
    assert out["conditionType"] == "and"
    assert [c["left"]["columnName"] for c in out["conditions"]] == ["a", "b"]


def test_multiple_operators_on_one_field_are_anded():
    out = to_condition({"n": {"$gt": 1, "$lt": 9}})
    assert out["conditionType"] == "and"
    assert [c["operator"] for c in out["conditions"]] == [">", "<"]


def test_explicit_and_or():
    out = to_condition({"$or": [{"a": 1}, {"b": 2}]})
    assert out["conditionType"] == "or"
    assert len(out["conditions"]) == 2

    out = to_condition({"$and": [{"a": 1}, {"b": 2}]})
    assert out["conditionType"] == "and"


def test_logical_operator_with_one_branch_is_unwrapped():
    assert to_condition({"$and": [{"a": 1}]}) == to_condition({"a": 1})


def test_logical_operator_with_empty_list_is_none():
    assert to_condition({"$or": []}) is None


def test_logical_operator_requires_a_list():
    with pytest.raises(ValueError, match=r"\$or expects a list"):
        to_condition({"$or": {"a": 1}})


def test_in_requires_a_list():
    with pytest.raises(ValueError, match=r"\$in expects a list"):
        to_condition({"a": {"$in": 5}})


def test_unsupported_operator_raises():
    with pytest.raises(ValueError, match=r"unsupported filter operator '\$exists'"):
        to_condition({"a": {"$exists": True}})


def test_unsupported_top_level_operator_raises():
    with pytest.raises(ValueError, match=r"unsupported filter operator '\$where'"):
        to_condition({"$where": "this.a == 1"})


def test_to_sort_translates_directions():
    assert to_sort({"title": 1, "n": -1}) == [
        {"columnName": "title", "direction": "ASC"},
        {"columnName": "n", "direction": "DESC"},
    ]


def test_to_sort_empty_is_none():
    assert to_sort(None) is None
    assert to_sort({}) is None
