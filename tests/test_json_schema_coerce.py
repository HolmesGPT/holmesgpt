"""Tests for :mod:`holmes.core.json_schema_coerce` — the standalone coercion utility."""

import pytest

from holmes.core.json_schema_coerce import coerce_params, drop_unset_optionals
from holmes.core.tools import ToolParameter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _schema(**fields: ToolParameter) -> dict:
    return fields


# ---------------------------------------------------------------------------
# Stringified JSON → array / object  (structural coercions)
# ---------------------------------------------------------------------------

class TestStringifiedJsonCoercion:
    def test_stringified_array(self):
        result = coerce_params(
            {"metrics": '["cpu", "memory"]'},
            _schema(metrics=ToolParameter(type="array", items=ToolParameter(type="string"))),
        )
        assert result["metrics"] == ["cpu", "memory"]

    def test_stringified_object(self):
        result = coerce_params(
            {"filters": '{"key": "value"}'},
            _schema(filters=ToolParameter(type="object")),
        )
        assert result["filters"] == {"key": "value"}

    def test_no_coercion_for_string_param(self):
        result = coerce_params(
            {"query": '["not", "an", "array"]'},
            _schema(query=ToolParameter(type="string")),
        )
        assert result["query"] == '["not", "an", "array"]'

    def test_no_coercion_when_already_correct_type(self):
        original = ["cpu", "memory"]
        result = coerce_params(
            {"metrics": original},
            _schema(metrics=ToolParameter(type="array")),
        )
        assert result["metrics"] is original

    def test_invalid_json_left_alone(self):
        result = coerce_params(
            {"metrics": "not json at all"},
            _schema(metrics=ToolParameter(type="array")),
        )
        # Falls through to single-value array wrap
        assert result["metrics"] == ["not json at all"]

    def test_parsed_type_mismatch_array_vs_object(self):
        """String parses as JSON array but schema says object — no coercion."""
        result = coerce_params(
            {"data": '["an", "array"]'},
            _schema(data=ToolParameter(type="object")),
        )
        assert result["data"] == '["an", "array"]'

    def test_nullable_array(self):
        result = coerce_params(
            {"metrics": '["cpu"]'},
            _schema(metrics=ToolParameter(type=["array", "null"])),
        )
        assert result["metrics"] == ["cpu"]


# ---------------------------------------------------------------------------
# Single value → array wrap
# ---------------------------------------------------------------------------

class TestSingleValueArrayWrap:
    def test_wrap_string_in_array(self):
        result = coerce_params(
            {"metrics": "cpu"},
            _schema(metrics=ToolParameter(type="array", items=ToolParameter(type="string"))),
        )
        assert result["metrics"] == ["cpu"]

    def test_wrap_int_in_array(self):
        result = coerce_params(
            {"ids": 42},
            _schema(ids=ToolParameter(type="array", items=ToolParameter(type="integer"))),
        )
        assert result["ids"] == [42]

    def test_already_a_list_not_wrapped(self):
        result = coerce_params(
            {"metrics": ["cpu"]},
            _schema(metrics=ToolParameter(type="array")),
        )
        assert result["metrics"] == ["cpu"]

    def test_nullable_array_wrap(self):
        result = coerce_params(
            {"tags": "important"},
            _schema(tags=ToolParameter(type=["array", "null"])),
        )
        assert result["tags"] == ["important"]


# ---------------------------------------------------------------------------
# String → integer
# ---------------------------------------------------------------------------

class TestStringToInteger:
    def test_whole_number(self):
        result = coerce_params(
            {"count": "42"},
            _schema(count=ToolParameter(type="integer")),
        )
        assert result["count"] == 42
        assert isinstance(result["count"], int)

    def test_float_string_rejected(self):
        """'3.7' should NOT be truncated to 3 — that's lossy."""
        result = coerce_params(
            {"count": "3.7"},
            _schema(count=ToolParameter(type="integer")),
        )
        assert result["count"] == "3.7"

    def test_float_string_whole_number(self):
        """'3.0' is a whole number expressed as float — safe to coerce."""
        result = coerce_params(
            {"count": "3.0"},
            _schema(count=ToolParameter(type="integer")),
        )
        assert result["count"] == 3
        assert isinstance(result["count"], int)

    def test_non_numeric_string(self):
        result = coerce_params(
            {"count": "abc"},
            _schema(count=ToolParameter(type="integer")),
        )
        assert result["count"] == "abc"

    def test_already_int(self):
        result = coerce_params(
            {"count": 5},
            _schema(count=ToolParameter(type="integer")),
        )
        assert result["count"] == 5

    def test_negative_integer(self):
        result = coerce_params(
            {"offset": "-10"},
            _schema(offset=ToolParameter(type="integer")),
        )
        assert result["offset"] == -10

    def test_strict_mode_skips_string_to_int(self):
        result = coerce_params(
            {"count": "42"},
            _schema(count=ToolParameter(type="integer")),
            strict=True,
        )
        assert result["count"] == "42"


# ---------------------------------------------------------------------------
# String → number (float)
# ---------------------------------------------------------------------------

class TestStringToNumber:
    def test_float_string(self):
        result = coerce_params(
            {"threshold": "3.14"},
            _schema(threshold=ToolParameter(type="number")),
        )
        assert result["threshold"] == pytest.approx(3.14)

    def test_integer_string_to_number(self):
        result = coerce_params(
            {"threshold": "42"},
            _schema(threshold=ToolParameter(type="number")),
        )
        assert result["threshold"] == 42.0

    def test_non_numeric_string(self):
        result = coerce_params(
            {"threshold": "high"},
            _schema(threshold=ToolParameter(type="number")),
        )
        assert result["threshold"] == "high"

    def test_already_float(self):
        result = coerce_params(
            {"threshold": 3.14},
            _schema(threshold=ToolParameter(type="number")),
        )
        assert result["threshold"] == pytest.approx(3.14)

    def test_already_int_for_number(self):
        """int is a valid Python type for JSON Schema 'number'."""
        result = coerce_params(
            {"threshold": 3},
            _schema(threshold=ToolParameter(type="number")),
        )
        assert result["threshold"] == 3

    def test_strict_mode_skips_string_to_number(self):
        result = coerce_params(
            {"threshold": "3.14"},
            _schema(threshold=ToolParameter(type="number")),
            strict=True,
        )
        assert result["threshold"] == "3.14"


# ---------------------------------------------------------------------------
# String → boolean
# ---------------------------------------------------------------------------

class TestStringToBoolean:
    def test_true_string(self):
        result = coerce_params(
            {"verbose": "true"},
            _schema(verbose=ToolParameter(type="boolean")),
        )
        assert result["verbose"] is True

    def test_false_string(self):
        result = coerce_params(
            {"verbose": "false"},
            _schema(verbose=ToolParameter(type="boolean")),
        )
        assert result["verbose"] is False

    def test_case_insensitive(self):
        result = coerce_params(
            {"verbose": "True"},
            _schema(verbose=ToolParameter(type="boolean")),
        )
        assert result["verbose"] is True

    def test_ambiguous_string_not_coerced(self):
        """'0', '1', 'yes', 'no' — too ambiguous, leave as string."""
        for val in ("0", "1", "yes", "no"):
            result = coerce_params(
                {"flag": val},
                _schema(flag=ToolParameter(type="boolean")),
            )
            assert result["flag"] == val, f"Should not coerce '{val}'"

    def test_already_bool(self):
        result = coerce_params(
            {"verbose": True},
            _schema(verbose=ToolParameter(type="boolean")),
        )
        assert result["verbose"] is True

    def test_strict_mode_skips_string_to_bool(self):
        result = coerce_params(
            {"verbose": "true"},
            _schema(verbose=ToolParameter(type="boolean")),
            strict=True,
        )
        assert result["verbose"] == "true"


# ---------------------------------------------------------------------------
# Edge cases and general behavior
# ---------------------------------------------------------------------------

class TestGeneralBehavior:
    def test_empty_params(self):
        result = coerce_params({}, _schema(metrics=ToolParameter(type="array")))
        assert result == {}

    def test_empty_schema(self):
        result = coerce_params({"metrics": '["cpu"]'}, {})
        assert result["metrics"] == '["cpu"]'

    def test_does_not_mutate_original(self):
        original = {"metrics": '["cpu"]'}
        result = coerce_params(original, _schema(metrics=ToolParameter(type="array")))
        assert original["metrics"] == '["cpu"]'
        assert result["metrics"] == ["cpu"]

    def test_param_not_in_schema_left_alone(self):
        result = coerce_params(
            {"unknown": "42", "count": "5"},
            _schema(count=ToolParameter(type="integer")),
        )
        assert result["unknown"] == "42"
        assert result["count"] == 5

    def test_multiple_params_coerced(self):
        result = coerce_params(
            {"count": "3", "verbose": "true", "tags": '["a","b"]'},
            _schema(
                count=ToolParameter(type="integer"),
                verbose=ToolParameter(type="boolean"),
                tags=ToolParameter(type="array"),
            ),
        )
        assert result["count"] == 3
        assert result["verbose"] is True
        assert result["tags"] == ["a", "b"]

    def test_bool_not_treated_as_int(self):
        """Python bool is subclass of int, but we should not treat True as
        a valid integer — leave it for the tool to decide."""
        result = coerce_params(
            {"count": True},
            _schema(count=ToolParameter(type="integer")),
        )
        # Bool passes through — we don't coerce bool→int
        assert result["count"] is True

    def test_works_with_raw_dict_schema(self):
        """coerce_params also accepts plain dicts as schema values."""
        result = coerce_params(
            {"count": "42"},
            {"count": {"type": "integer"}},
        )
        assert result["count"] == 42

    def test_raw_dict_nullable_type(self):
        result = coerce_params(
            {"tags": '["a"]'},
            {"tags": {"type": ["array", "null"]}},
        )
        assert result["tags"] == ["a"]


# ---------------------------------------------------------------------------
# null for optional params (strict tool calling sends null for "not set")
# ---------------------------------------------------------------------------

class TestNullOptionalParams:
    def test_optional_null_is_dropped(self):
        result = coerce_params(
            {"query": "up", "timeout": None},
            _schema(
                query=ToolParameter(type="string"),
                timeout=ToolParameter(type="integer", required=False),
            ),
        )
        assert result == {"query": "up"}

    def test_required_null_is_kept(self):
        result = coerce_params(
            {"query": None},
            _schema(query=ToolParameter(type="string")),
        )
        assert result == {"query": None}

    def test_declared_nullable_optional_null_is_kept(self):
        result = coerce_params(
            {"cursor": None},
            _schema(cursor=ToolParameter(type=["string", "null"], required=False)),
        )
        assert result == {"cursor": None}

    def test_any_of_with_null_branch_is_kept(self):
        param = ToolParameter(
            required=False,
            any_of=[ToolParameter(type="string"), ToolParameter(type="null")],
        )
        assert coerce_params({"v": None}, _schema(v=param)) == {"v": None}

    def test_any_of_without_null_branch_is_dropped(self):
        param = ToolParameter(
            required=False,
            any_of=[ToolParameter(type="string"), ToolParameter(type="integer")],
        )
        assert coerce_params({"v": None}, _schema(v=param)) == {}

    def test_optional_array_null_is_not_wrapped(self):
        schema = _schema(
            labels=ToolParameter(type="array", items=ToolParameter(type="string"), required=False)
        )
        assert coerce_params({"labels": None}, schema) == {}

    def test_nullable_array_null_stays_null(self):
        schema = _schema(
            labels=ToolParameter(type=["array", "null"], items=ToolParameter(type="string"))
        )
        assert coerce_params({"labels": None}, schema) == {"labels": None}

    def test_nested_object_optional_null_is_dropped(self):
        schema = _schema(
            filter=ToolParameter(
                type="object",
                properties={
                    "name": ToolParameter(type="string"),
                    "limit": ToolParameter(type="integer", required=False),
                    "owner": ToolParameter(type="string"),
                },
            )
        )
        result = coerce_params(
            {"filter": {"name": "a", "limit": None, "owner": None}}, schema
        )
        assert result == {"filter": {"name": "a", "owner": None}}

    def test_array_of_objects_optional_null_is_dropped(self):
        schema = _schema(
            items=ToolParameter(
                type="array",
                items=ToolParameter(
                    type="object",
                    properties={
                        "key": ToolParameter(type="string"),
                        "op": ToolParameter(type="string", required=False),
                    },
                ),
            )
        )
        result = coerce_params(
            {"items": [{"key": "a", "op": None}, {"key": "b", "op": "eq"}, "x"]}, schema
        )
        assert result == {"items": [{"key": "a"}, {"key": "b", "op": "eq"}, "x"]}

    def test_unknown_param_null_is_kept(self):
        assert coerce_params({"extra": None}, _schema(q=ToolParameter())) == {"extra": None}

    def test_input_is_not_mutated(self):
        params = {"timeout": None, "filter": {"limit": None}}
        schema = _schema(
            timeout=ToolParameter(type="integer", required=False),
            filter=ToolParameter(
                type="object",
                properties={"limit": ToolParameter(type="integer", required=False)},
            ),
        )
        assert coerce_params(params, schema) == {"filter": {}}
        assert params == {"timeout": None, "filter": {"limit": None}}

    def test_dict_schema_optional_null_is_dropped(self):
        schema = {"limit": {"type": "integer", "required": False}}
        assert coerce_params({"limit": None}, schema) == {}

    def test_arrays_without_nulls_are_returned_as_is(self):
        schema = _schema(
            objs=ToolParameter(
                type="array",
                items=ToolParameter(
                    type="object", properties={"op": ToolParameter(type="string", required=False)}
                ),
            ),
            names=ToolParameter(type="array", items=ToolParameter(type="string")),
        )
        params = {"objs": [{"op": "eq"}], "names": ["a", None]}
        assert drop_unset_optionals(params, schema) is params

    def test_drop_unset_optionals_without_schema(self):
        params = {"timeout": None}
        assert drop_unset_optionals(params, {}) is params

    def test_stringified_object_optional_null_is_dropped(self):
        schema = _schema(
            filter=ToolParameter(
                type="object",
                properties={
                    "name": ToolParameter(type="string"),
                    "limit": ToolParameter(type="integer", required=False),
                },
            )
        )
        result = coerce_params({"filter": '{"name": "a", "limit": null}'}, schema)
        assert result == {"filter": {"name": "a"}}

    def test_any_of_object_branch_optional_null_is_dropped(self):
        param = ToolParameter(
            any_of=[
                ToolParameter(type="string"),
                ToolParameter(
                    type="object",
                    properties={
                        "name": ToolParameter(type="string"),
                        "limit": ToolParameter(type="integer", required=False),
                    },
                ),
            ]
        )
        result = coerce_params({"v": {"name": "a", "limit": None}}, _schema(v=param))
        assert result == {"v": {"name": "a"}}

    def test_any_of_picks_the_branch_that_declares_every_key(self):
        param = ToolParameter(
            any_of=[
                ToolParameter(
                    type="object",
                    properties={"id": ToolParameter(type="string")},
                ),
                ToolParameter(
                    type="object",
                    properties={
                        "name": ToolParameter(type="string"),
                        "limit": ToolParameter(type="integer", required=False),
                    },
                ),
            ]
        )
        result = coerce_params({"v": {"name": "a", "limit": None}}, _schema(v=param))
        assert result == {"v": {"name": "a"}}

    def test_any_of_without_matching_branch_is_left_alone(self):
        param = ToolParameter(
            any_of=[
                ToolParameter(
                    type="object",
                    properties={"limit": ToolParameter(type="integer", required=False)},
                ),
            ]
        )
        params = {"v": {"limit": None, "other": 1}}
        assert drop_unset_optionals(params, _schema(v=param)) is params

    def test_array_items_any_of_branch_optional_null_is_dropped(self):
        schema = _schema(
            objs=ToolParameter(
                type="array",
                items=ToolParameter(
                    any_of=[
                        ToolParameter(type="string"),
                        ToolParameter(
                            type="object",
                            properties={"op": ToolParameter(type="string", required=False)},
                        ),
                    ]
                ),
            )
        )
        result = coerce_params({"objs": [{"op": None}, "x"]}, schema)
        assert result == {"objs": [{}, "x"]}
