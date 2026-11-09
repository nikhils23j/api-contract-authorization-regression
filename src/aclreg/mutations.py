from dataclasses import dataclass

from .contract import expected_status


@dataclass(frozen=True)
class Mutation:
    name: str
    kind: str
    user: str = ""
    document_id: str = ""
    method: str = ""
    field: str = ""


def all_mutations(contract):
    """Deterministic defects: 40 authorization, 10 schema, 8 list, 2 revision."""
    denied = []
    for user in sorted(contract["users"]):
        for document_id in sorted(contract["documents"]):
            for method in ("GET", "PATCH", "DELETE"):
                status = expected_status(contract, user, document_id, method)
                if status in (403, 404):
                    denied.append((user, document_id, method))
    assert len(denied) >= 40
    selected = [denied[(index * len(denied)) // 40] for index in range(40)]
    result = [
        Mutation(f"auth-{index:02d}-{user}-{method.lower()}-{doc}", "auth", user, doc, method)
        for index, (user, doc, method) in enumerate(selected, 1)
    ]
    fields = tuple(contract["response_fields"])
    for document_id in ("a1", "b1"):
        for field in fields:
            result.append(Mutation(f"schema-{document_id}-{field}", "schema", document_id=document_id, field=field))
    for user in sorted(contract["users"]):
        result.append(Mutation(f"list-leak-{user}", "list", user=user))
    for document_id, owner in (("a1", "alice"), ("b1", "carol")):
        result.append(Mutation(f"stale-write-{document_id}", "revision", user=owner, document_id=document_id))
    assert len(result) == 60
    assert len({mutation.name for mutation in result}) == 60
    return result
