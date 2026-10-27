"""Making a JSON Schema acceptable to the strictest provider.

The three CLIs do not agree on what a response schema may look like. Claude and
Antigravity accept ordinary JSON Schema, where a property absent from ``required`` is
simply optional. Codex uses OpenAI's *strict* structured-output mode, which rejects that
outright::

    'required' is required to be supplied and to be an array including every key in
    properties. Missing 'new_value'.

So a schema written the natural way works for two providers and fails for the third --
and it fails at the point of use, on a real turn, having already spent the call. Since a
strict schema is also a perfectly valid ordinary one, the resolution is to write to the
strictest dialect and use it everywhere, rather than maintaining a variant per provider.

The transformation is mechanical: every property becomes required, and anything that was
optional becomes nullable so a model can still decline to supply it.
"""

from __future__ import annotations

from typing import Any

__all__ = ["make_strict"]


def _widen_type(node: dict[str, Any]) -> dict[str, Any]:
    declared = node.get("type")
    if declared is None:
        return node
    types = list(declared) if isinstance(declared, list) else [declared]
    if "null" not in types:
        types.append("null")
    return {**node, "type": types}


def _nullable(node: dict[str, Any]) -> dict[str, Any]:
    """Allow ``null`` for a property that used to be optional.

    **Enums are deliberately left alone, and the two providers are why.** Codex rejects a
    schema where an enum contains ``None`` but the declared type does not admit null;
    Antigravity rejects any enum containing ``null`` at all, whatever the type says. There
    is no nullable-enum spelling that both accept.

    So an optional enum becomes a *required* enum with no null member. The model must pick
    one of the listed values rather than omitting the field -- which is a small semantic
    change and arguably the better one: ``visibility_scope`` and ``tone`` are exactly the
    fields where a considered answer beats a silent default.
    """
    if "enum" in node:
        return node
    return _widen_type(node)


def make_strict(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a copy where every object lists all its properties as required.

    Optional properties are made nullable rather than mandatory in substance: the model
    must mention the key, but ``null`` remains a legitimate answer, which preserves the
    original meaning.
    """
    if not isinstance(schema, dict):
        return schema

    out: dict[str, Any] = dict(schema)

    if out.get("type") == "object" and isinstance(out.get("properties"), dict):
        properties: dict[str, Any] = {}
        previously_required = set(out.get("required") or ())

        for name, child in out["properties"].items():
            converted = make_strict(child) if isinstance(child, dict) else child
            if name not in previously_required and isinstance(converted, dict):
                converted = _nullable(converted)
            properties[name] = converted

        out["properties"] = properties
        out["required"] = list(properties)
        out.setdefault("additionalProperties", False)

    if isinstance(out.get("items"), dict):
        out["items"] = make_strict(out["items"])

    for key in ("anyOf", "oneOf", "allOf"):
        if isinstance(out.get(key), list):
            out[key] = [make_strict(entry) if isinstance(entry, dict) else entry for entry in out[key]]

    return out
