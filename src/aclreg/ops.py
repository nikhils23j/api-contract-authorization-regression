"""Operation language shared by every strategy.

An op is a small tuple such as ("share", "alice", 3, "erin", "editor").
Documents and comments are referenced by *slot numbers*, resolved against the
model's list of every id ever issued (slot % len). That keeps sequences
replayable after the shrinker deletes steps: a slot always points somewhere.
"""
from dataclasses import dataclass
from typing import Any, Callable, Optional

USERS = ["alice", "bob", "erin", "amy", "carol", "dave", "mike", "ben"]


@dataclass
class Step:
    method: str
    path: str
    user: Optional[str]
    body: Any
    if_match: Optional[str]
    expect: Callable  # () -> model.Resp, called *after* the service request
    text: str
    list_actor: Optional[str] = None  # set for list ops so cursors can be stored


def _doc(model, slot):
    return model.doc_ids[slot % len(model.doc_ids)]


def _comment(model, slot):
    return model.comment_ids[slot % len(model.comment_ids)] if model.comment_ids else "c999"


def _etag(model, doc_id, kind):
    version = model.docs[doc_id]["version"] if doc_id in model.docs else 1
    if kind == "missing":
        return None
    if kind == "stale":
        version -= 1
    return f'"v{version}"'


def _doc_body(kind, actor):
    other = "ben" if actor != "ben" else "alice"
    other_tenant = "alpha" if actor in ("carol", "dave", "mike", "ben") else "beta"
    return {
        "ok": {"title": "Edited"},
        "extra_owner": {"title": "Edited", "owner": other},
        "extra_tenant": {"title": "Edited", "tenant": other_tenant},
        "extra_version": {"title": "Edited", "version": 99},
        "bad": {"title": ""},
    }[kind]


def resolve(op, model, cursors):
    """Turn a symbolic op into a concrete HTTP request + model call."""
    name, actor = op[0], op[1]
    who = actor or "anonymous"
    if name == "list":
        _, _, limit, cursor_from = op
        source = actor if cursor_from == "self" else cursor_from
        pair = cursors.get(source) if source else None
        path = f"/v1/documents?limit={limit}"
        text = f"{who} lists documents (limit={limit})"
        model_cursor = None
        if pair:
            path += f"&cursor={pair[0]}"
            model_cursor = pair[1]
            text += " using " + ("their own next cursor" if source == actor else f"{source}'s cursor")
        return Step("GET", path, actor, None, None,
                    lambda: model.list_docs(actor, limit, model_cursor), text, list_actor=actor)
    if name == "create":
        body = _doc_body(op[2], actor)
        return Step("POST", "/v1/documents", actor, body, None,
                    lambda: model.create_doc(actor, body), f"{who} creates a document with body {body}")
    if name == "get":
        d = _doc(model, op[2])
        return Step("GET", f"/v1/documents/{d}", actor, None, None,
                    lambda: model.get_doc(actor, d), f"{who} GETs {d}")
    if name == "patch":
        d = _doc(model, op[2])
        tag, body = _etag(model, d, op[3]), _doc_body(op[4], actor)
        return Step("PATCH", f"/v1/documents/{d}", actor, body, tag,
                    lambda: model.patch_doc(actor, d, body, tag),
                    f"{who} PATCHes {d} with {body} (If-Match {tag or 'missing'})")
    if name == "delete":
        d = _doc(model, op[2])
        tag = _etag(model, d, op[3])
        return Step("DELETE", f"/v1/documents/{d}", actor, None, tag,
                    lambda: model.delete_doc(actor, d, tag),
                    f"{who} DELETEs {d} (If-Match {tag or 'missing'})")
    if name == "share":
        d, grantee, role = _doc(model, op[2]), op[3], op[4]
        body = {"role": role}
        return Step("PUT", f"/v1/documents/{d}/shares/{grantee}", actor, body, None,
                    lambda: model.share(actor, d, grantee, body),
                    f"{who} shares {d} with {grantee} as {role}")
    if name == "revoke":
        d, grantee = _doc(model, op[2]), op[3]
        return Step("DELETE", f"/v1/documents/{d}/shares/{grantee}", actor, None, None,
                    lambda: model.revoke(actor, d, grantee), f"{who} revokes {grantee}'s share on {d}")
    if name == "comments":
        d = _doc(model, op[2])
        return Step("GET", f"/v1/documents/{d}/comments", actor, None, None,
                    lambda: model.list_comments(actor, d), f"{who} lists comments on {d}")
    if name == "comment":
        d = _doc(model, op[2])
        body = {"text": "hi" if op[3] == "ok" else ""}
        return Step("POST", f"/v1/documents/{d}/comments", actor, body, None,
                    lambda: model.add_comment(actor, d, body), f"{who} comments {body['text']!r} on {d}")
    if name in ("get_comment", "delete_comment"):
        c = _comment(model, op[3])
        d = model.comment_parent.get(c, "d001") if op[2] is None else _doc(model, op[2])
        method = "GET" if name == "get_comment" else "DELETE"
        fn = model.get_comment if name == "get_comment" else model.delete_comment
        return Step(method, f"/v1/documents/{d}/comments/{c}", actor, None, None,
                    lambda: fn(actor, d, c), f"{who} {method}s comment {c} via /documents/{d}")
    raise ValueError(f"unknown op {op!r}")


# ----- random generation (stateful strategy) --------------------------------
_WEIGHTS = [("get", 14), ("list", 12), ("create", 8), ("patch", 14), ("delete", 5),
            ("share", 11), ("revoke", 7), ("comments", 6), ("comment", 8),
            ("get_comment", 9), ("delete_comment", 6)]


def _pick_doc_slot(rng, model, tenant):
    """Guided mode: prefer live documents in the actor's tenant, sometimes anything."""
    r = rng.random()
    live = [i for i, d in enumerate(model.doc_ids) if d in model.docs]
    local = [i for i in live if model.docs[model.doc_ids[i]]["tenant"] == tenant]
    if r < 0.6 and local:
        return rng.choice(local)
    if r < 0.85 and live:
        return rng.choice(live)
    return rng.randrange(1000)


def random_op(rng, model=None):
    """Draw one op. With `model=None` arguments are uniform ("uniform" mode);
    with a model the generator peeks at current state to pick plausible
    documents, grantees and comments ("guided" mode). Either way the op is a
    plain tuple, so replay and shrinking work identically."""
    name = rng.choices([n for n, _ in _WEIGHTS], [w for _, w in _WEIGHTS])[0]
    actor = None if rng.random() < 0.02 else rng.choice(USERS)
    if model is None:
        slot = rng.randrange(1000)
        grantee = rng.choice(USERS)
        comment_slot = rng.randrange(1000)
    else:
        tenant = model.users[actor]["tenant"] if actor else "alpha"
        slot = _pick_doc_slot(rng, model, tenant)
        doc_id = model.doc_ids[slot % len(model.doc_ids)]
        doc = model.docs.get(doc_id)
        doc_tenant = doc["tenant"] if doc else tenant
        same = [u for u in USERS if model.users[u]["tenant"] == doc_tenant]
        grantee = rng.choice(same) if rng.random() < 0.7 else rng.choice(USERS)
        if name == "revoke" and rng.random() < 0.7:
            grants = [(i, u) for i, d in enumerate(model.doc_ids) if d in model.shares
                      for u in model.shares[d]]
            if grants:
                slot, grantee = rng.choice(grants)
                doc = model.docs[model.doc_ids[slot]]
        if doc and actor is not None and rng.random() < 0.5:
            # act as someone involved with this document: owner, a grantee, or the tenant admin
            involved = [doc["owner"]] + list(model.shares.get(model.doc_ids[slot % len(model.doc_ids)], {}))
            involved += [u for u in same if model.users[u]["role"] == "admin"]
            if name == "list" and rng.random() < 0.5:
                involved = [grantee]
            actor = rng.choice(involved)
        live = [i for i, c in enumerate(model.comment_ids) if c in model.comments]
        comment_slot = rng.choice(live) if live and rng.random() < 0.7 else rng.randrange(1000)
    if name == "list":
        limit = rng.choice([1, 2, 3, 50])
        r = rng.random()
        cursor_from = None if r < 0.4 else ("self" if r < 0.85 else rng.choice(USERS))
        return ("list", actor, limit, cursor_from)
    if name == "create":
        return ("create", actor, rng.choices(["ok", "extra_owner", "extra_tenant", "bad"], [80, 7, 7, 6])[0])
    if name in ("get", "comments"):
        return (name, actor, slot)
    if name == "patch":
        return ("patch", actor, slot, rng.choices(["current", "stale", "missing"], [75, 15, 10])[0],
                rng.choices(["ok", "extra_owner", "extra_version", "bad"], [80, 7, 7, 6])[0])
    if name == "delete":
        return ("delete", actor, slot, rng.choices(["current", "stale", "missing"], [75, 15, 10])[0])
    if name == "share":
        return ("share", actor, slot, grantee, rng.choice(["viewer", "editor"]))
    if name == "revoke":
        return ("revoke", actor, slot, grantee)
    if name == "comment":
        return ("comment", actor, slot, "ok" if rng.random() < 0.9 else "bad")
    doc_slot = None if rng.random() < 0.65 else slot
    return (name, actor, doc_slot, comment_slot)
