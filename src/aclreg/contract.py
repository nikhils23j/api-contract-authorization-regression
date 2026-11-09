"""Fixture loading and schema checks (independent of both service and model)."""
import json
from pathlib import Path

CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "documents.json"
_KINDS = {"str": str, "int": int}


def load_contract():
    with CONTRACT.open(encoding="utf-8") as handle:
        return json.load(handle)


def matches_schema(contract, name, payload):
    fields = contract["schemas"][name]
    if not isinstance(payload, dict) or set(payload) != set(fields):
        return False
    return all(type(payload[key]) is _KINDS[kind] for key, kind in fields.items())


def schema_problems(contract, kind, payload):
    """Return a list of schema problems for a successful response body of `kind`."""
    if kind is None:
        return [] if payload is None else ["expected empty body"]
    if kind == "list":
        if not isinstance(payload, dict) or set(payload) != {"items", "next_cursor"}:
            return ["list body must have exactly items/next_cursor"]
        if payload["next_cursor"] is not None and not isinstance(payload["next_cursor"], str):
            return ["next_cursor must be str or null"]
        bad = [item for item in payload["items"] if not matches_schema(contract, "document", item)]
        return ["list item violates document schema"] if bad else []
    if kind == "comment_list":
        if not isinstance(payload, dict) or set(payload) != {"items"}:
            return ["comment list must have exactly items"]
        bad = [item for item in payload["items"] if not matches_schema(contract, "comment", item)]
        return ["comment item violates comment schema"] if bad else []
    return [] if matches_schema(contract, kind, payload) else [f"body violates {kind} schema"]
