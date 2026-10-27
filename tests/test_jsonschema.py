"""Schemas that satisfy the strictest provider.

Codex uses OpenAI's strict structured-output mode; Claude and agy accept ordinary JSON
Schema. A schema written the natural way therefore works for two providers and is rejected
by the third -- mid-turn, after the call has already been paid for. Both rejections below
were observed against the real CLI before these tests existed.
"""

from __future__ import annotations

import pytest

from atlas.engine.diplomacy import COMMITMENT_SCHEMA
from atlas.engine.eve import _bloc_schema
from atlas.engine.parser import ACTION_SCHEMA
from atlas.state.diff import diff_batch_schema
from atlas.state.jsonschema import make_strict
from atlas.state.models import Role


def _violations(node, path: str = "") -> list[str]:
    """Every place a schema breaks the strict dialect."""
    problems: list[str] = []
    if isinstance(node, dict):
        if node.get("type") == "object" and isinstance(node.get("properties"), dict):
            missing = set(node["properties"]) - set(node.get("required") or ())
            if missing:
                problems.append(f"{path or 'root'}: {sorted(missing)} absent from required")
            if node.get("additionalProperties") is not False:
                problems.append(f"{path or 'root'}: additionalProperties is not false")

        # A null member in an enum is only legal if the declared type admits null.
        if "enum" in node and None in (node.get("enum") or ()):
            declared = node.get("type")
            types = declared if isinstance(declared, list) else [declared]
            if declared is not None and "null" not in types:
                problems.append(f"{path}: enum allows null but type does not")

        for key, value in node.items():
            problems.extend(_violations(value, f"{path}.{key}" if path else key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            problems.extend(_violations(value, f"{path}[{index}]"))
    return problems


@pytest.mark.parametrize(
    ("name", "schema"),
    [
        ("diff/world_sim", diff_batch_schema(role=Role.WORLD_SIM)),
        ("diff/tracked_agent", diff_batch_schema(role=Role.TRACKED_AGENT)),
        ("diff/unscoped", diff_batch_schema()),
        ("actions", ACTION_SCHEMA),
        ("commitments", COMMITMENT_SCHEMA),
        ("eve bloc, gambits allowed", _bloc_schema(True)),
        ("eve bloc, gambits off", _bloc_schema(False)),
    ],
)
def test_every_shipped_schema_is_strict(name, schema):
    assert not _violations(schema), f"{name}: {_violations(schema)}"


def test_optional_properties_become_required_but_nullable():
    """Meaning is preserved: the key must be mentioned, `null` remains a valid answer."""
    strict = make_strict({
        "type": "object",
        "required": ["needed"],
        "properties": {"needed": {"type": "string"}, "optional": {"type": "string"}},
    })
    assert set(strict["required"]) == {"needed", "optional"}
    assert strict["properties"]["optional"]["type"] == ["string", "null"]
    assert strict["properties"]["needed"]["type"] == "string", "a required field stays as it was"


def test_an_optional_enum_becomes_required_without_a_null_member():
    """The two providers cannot both be satisfied by a nullable enum.

    Codex rejects an enum containing ``None`` when the type does not admit null. agy
    rejects any enum containing null at all, whatever the type says. There is no spelling
    both accept, so an optional enum becomes a required one with no null member -- the
    model picks a value rather than omitting the field.
    """
    strict = make_strict({
        "type": "object",
        "required": [],
        "properties": {"tone": {"type": "string", "enum": ["firm", "warm"]}},
    })
    tone = strict["properties"]["tone"]
    assert "tone" in strict["required"]
    assert None not in tone["enum"]
    assert tone["type"] == "string"


def test_no_shipped_schema_has_a_null_inside_an_enum():
    """agy rejects the whole call if one does, and the message is generic."""
    def offenders(node, path=""):
        found = []
        if isinstance(node, dict):
            if None in (node.get("enum") or ()):
                found.append(path or "root")
            for key, value in node.items():
                found.extend(offenders(value, f"{path}.{key}" if path else key))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                found.extend(offenders(value, f"{path}[{index}]"))
        return found

    for name, schema in (
        ("diff", diff_batch_schema(role=Role.WORLD_SIM)),
        ("actions", ACTION_SCHEMA),
        ("commitments", COMMITMENT_SCHEMA),
    ):
        assert not offenders(schema), f"{name}: {offenders(schema)}"


def test_nested_objects_and_arrays_are_converted():
    strict = make_strict({
        "type": "object",
        "required": ["items"],
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["a"],
                    "properties": {"a": {"type": "string"}, "b": {"type": "number"}},
                },
            }
        },
    })
    inner = strict["properties"]["items"]["items"]
    assert set(inner["required"]) == {"a", "b"}
    assert inner["additionalProperties"] is False


def test_conversion_is_idempotent():
    once = make_strict(ACTION_SCHEMA)
    twice = make_strict(once)
    assert once == twice


def test_the_field_enum_names_real_columns():
    """The other rejection class: models writing the labels from their briefing.

    A view saying "regard: +12" led to `field: "regard"`, which is not a column, and the
    batch was refused. Naming the columns in the schema means the CLI cannot emit one.
    """
    schema = diff_batch_schema(role=Role.WORLD_SIM)
    fields = schema["properties"]["diffs"]["items"]["properties"]["field"]["enum"]

    assert "military_strength" in fields
    assert "score" in fields
    assert "regard" not in fields, "a briefing label must not be offered as a column"
    assert "military" not in fields


def test_the_field_enum_excludes_engine_only_columns():
    """The write matrix reaches the schema, so CORE-003 becomes a backstop not the gate."""
    schema = diff_batch_schema(role=Role.WORLD_SIM)
    fields = schema["properties"]["diffs"]["items"]["properties"]["field"]["enum"]
    tables = schema["properties"]["diffs"]["items"]["properties"]["target_table"]["enum"]

    assert "owner_nation_id" not in fields, "territory changes hands only through resolution"
    assert "at_war" not in fields
    assert "rng_seed" not in fields
    assert "unit" not in tables, "models cannot address units at all"


def test_a_role_with_no_write_rights_gets_no_targets():
    """The parser emits actions, never diffs."""
    from atlas.state.diff import writable_fields_for

    assert writable_fields_for(Role.PARSER) == {}
