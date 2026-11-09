import json
from pathlib import Path


CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "documents.json"


def load_contract():
    with CONTRACT.open(encoding="utf-8") as handle:
        return json.load(handle)


def expected_status(contract, user, document_id, method):
    """Independent oracle derived from the declarative contract."""
    if user is None:
        return 401
    principal = contract["users"][user]
    document = contract["documents"][document_id]
    if principal["tenant"] != document["tenant"]:
        return 404
    if method == "GET":
        return 200
    if principal["role"] == "admin" or document["owner"] == user:
        return 200 if method == "PATCH" else 204
    return 403


def expected_list_ids(contract, user):
    tenant = contract["users"][user]["tenant"]
    return sorted(
        doc_id
        for doc_id, document in contract["documents"].items()
        if document["tenant"] == tenant
    )


def validate_document(contract, payload):
    fields = contract["response_fields"]
    if not isinstance(payload, dict) or set(payload) != set(fields):
        return False
    kinds = {"str": str, "int": int}
    return all(type(payload[name]) is kinds[kind] for name, kind in fields.items())
