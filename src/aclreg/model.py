"""Pure reference model of the document API.

No sockets, no threads, no shared code with service.py. Every operation is a
plain function of (state, request) -> response. The test strategies compare
the real HTTP service against this model, so this file *is* the spec.

Policy, in words:
  * unknown / missing X-User                  -> 401 on every endpoint
  * can_read  = same tenant AND (admin OR owner OR has any share)
  * can_write = can_read AND role != viewer AND (admin OR owner OR editor share)
  * can_manage (delete doc, share, revoke) = can_read AND (admin OR owner)
  * a document you cannot read is indistinguishable from a missing one -> 404
  * readable but not allowed -> 403
  * PATCH / DELETE need If-Match: "v<version>"   (428 missing, 412 stale)
  * request bodies are strict: unexpected keys -> 400 (no mass assignment)
  * list cursors are bound to the user they were issued to (other user -> 400)
"""
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class Resp:
    status: int
    body: Any = None
    etag: Optional[str] = None
    kind: Optional[str] = None  # schema name for 2xx bodies

    @property
    def next_cursor(self):
        if isinstance(self.body, dict):
            return self.body.get("next_cursor")
        return None


def _err(status, message):
    return Resp(status, {"error": message})


def _title_ok(value, limit=80):
    return isinstance(value, str) and 1 <= len(value.strip()) <= limit


class Model:
    def __init__(self, contract):
        self.users = contract["users"]
        self.docs = {
            doc_id: dict(doc) for doc_id, doc in sorted(contract["documents"].items())
        }
        self.shares = {doc_id: {} for doc_id in self.docs}
        for doc_id, grants in contract.get("shares", {}).items():
            self.shares[doc_id] = dict(grants)
        self.comments = {cid: dict(c) for cid, c in sorted(contract.get("comments", {}).items())}
        # every id ever issued, in creation order (used to resolve "slots")
        self.doc_ids = list(self.docs)
        self.comment_ids = list(self.comments)
        self.comment_parent = {cid: c["document_id"] for cid, c in self.comments.items()}

    # ----- helpers ---------------------------------------------------------
    def _view(self, doc_id):
        doc = self.docs[doc_id]
        return {"id": doc_id, "tenant": doc["tenant"], "owner": doc["owner"],
                "title": doc["title"], "version": doc["version"]}

    def _comment_view(self, cid):
        c = self.comments[cid]
        return {"id": cid, "document_id": c["document_id"], "author": c["author"], "text": c["text"]}

    def _etag(self, doc_id):
        return f'"v{self.docs[doc_id]["version"]}"'

    def can_read(self, user, doc_id):
        if user not in self.users or doc_id not in self.docs:
            return False
        me, doc = self.users[user], self.docs[doc_id]
        if me["tenant"] != doc["tenant"]:
            return False
        return me["role"] == "admin" or doc["owner"] == user or user in self.shares[doc_id]

    def can_write(self, user, doc_id):
        if not self.can_read(user, doc_id) or self.users[user]["role"] == "viewer":
            return False
        return (self.users[user]["role"] == "admin" or self.docs[doc_id]["owner"] == user
                or self.shares[doc_id].get(user) == "editor")

    def can_manage(self, user, doc_id):
        return self.can_read(user, doc_id) and (
            self.users[user]["role"] == "admin" or self.docs[doc_id]["owner"] == user)

    def _precondition(self, doc_id, if_match):
        if if_match is None:
            return _err(428, "If-Match required")
        if if_match != self._etag(doc_id):
            return _err(412, "stale version")
        return None

    # ----- documents -------------------------------------------------------
    def list_docs(self, user, limit=10, cursor=None):
        if user not in self.users:
            return _err(401, "authentication required")
        if not isinstance(limit, int) or not 1 <= limit <= 50:
            return _err(400, "bad limit")
        after = None
        if cursor is not None:
            issuer, after = cursor
            if issuer != user:
                return _err(400, "cursor belongs to another user")
        visible = sorted(d for d in self.docs if self.can_read(user, d))
        remaining = [d for d in visible if after is None or d > after]
        page = remaining[:limit]
        nxt = (user, page[-1]) if len(remaining) > limit else None
        return Resp(200, {"items": [self._view(d) for d in page], "next_cursor": nxt}, kind="list")

    def create_doc(self, user, body):
        if user not in self.users:
            return _err(401, "authentication required")
        if self.users[user]["role"] == "viewer":
            return _err(403, "viewers cannot create")
        if not isinstance(body, dict) or set(body) != {"title"} or not _title_ok(body["title"]):
            return _err(400, "invalid body")
        doc_id = f"d{len(self.doc_ids) + 1:03d}"
        self.docs[doc_id] = {"tenant": self.users[user]["tenant"], "owner": user,
                             "title": body["title"], "version": 1}
        self.shares[doc_id] = {}
        self.doc_ids.append(doc_id)
        return Resp(201, self._view(doc_id), self._etag(doc_id), "document")

    def get_doc(self, user, doc_id):
        if user not in self.users:
            return _err(401, "authentication required")
        if not self.can_read(user, doc_id):
            return _err(404, "not found")
        return Resp(200, self._view(doc_id), self._etag(doc_id), "document")

    def patch_doc(self, user, doc_id, body, if_match):
        if user not in self.users:
            return _err(401, "authentication required")
        if not self.can_read(user, doc_id):
            return _err(404, "not found")
        if not self.can_write(user, doc_id):
            return _err(403, "forbidden")
        failed = self._precondition(doc_id, if_match)
        if failed:
            return failed
        if not isinstance(body, dict) or set(body) != {"title"} or not _title_ok(body["title"]):
            return _err(400, "invalid body")
        self.docs[doc_id]["title"] = body["title"]
        self.docs[doc_id]["version"] += 1
        return Resp(200, self._view(doc_id), self._etag(doc_id), "document")

    def delete_doc(self, user, doc_id, if_match):
        if user not in self.users:
            return _err(401, "authentication required")
        if not self.can_read(user, doc_id):
            return _err(404, "not found")
        if not self.can_manage(user, doc_id):
            return _err(403, "forbidden")
        failed = self._precondition(doc_id, if_match)
        if failed:
            return failed
        del self.docs[doc_id]
        del self.shares[doc_id]
        for cid in [c for c, v in self.comments.items() if v["document_id"] == doc_id]:
            del self.comments[cid]
        return Resp(204)

    # ----- shares ----------------------------------------------------------
    def share(self, user, doc_id, grantee, body):
        if user not in self.users:
            return _err(401, "authentication required")
        if not self.can_read(user, doc_id):
            return _err(404, "not found")
        if not self.can_manage(user, doc_id):
            return _err(403, "forbidden")
        if (not isinstance(body, dict) or set(body) != {"role"}
                or body["role"] not in ("viewer", "editor")):
            return _err(400, "invalid body")
        target = self.users.get(grantee)
        if (target is None or target["tenant"] != self.docs[doc_id]["tenant"]
                or grantee == self.docs[doc_id]["owner"]):
            return _err(400, "invalid grantee")
        self.shares[doc_id][grantee] = body["role"]
        return Resp(200, {"document_id": doc_id, "user": grantee, "role": body["role"]}, kind="share")

    def revoke(self, user, doc_id, grantee):
        if user not in self.users:
            return _err(401, "authentication required")
        if not self.can_read(user, doc_id):
            return _err(404, "not found")
        if not self.can_manage(user, doc_id):
            return _err(403, "forbidden")
        if grantee not in self.shares[doc_id]:
            return _err(404, "no such share")
        del self.shares[doc_id][grantee]
        return Resp(204)

    # ----- comments (nested resource) -------------------------------------
    def list_comments(self, user, doc_id):
        if user not in self.users:
            return _err(401, "authentication required")
        if not self.can_read(user, doc_id):
            return _err(404, "not found")
        items = [self._comment_view(c) for c in sorted(self.comments)
                 if self.comments[c]["document_id"] == doc_id]
        return Resp(200, {"items": items}, kind="comment_list")

    def add_comment(self, user, doc_id, body):
        if user not in self.users:
            return _err(401, "authentication required")
        if not self.can_read(user, doc_id):
            return _err(404, "not found")
        if not isinstance(body, dict) or set(body) != {"text"} or not _title_ok(body["text"], 200):
            return _err(400, "invalid body")
        cid = f"c{len(self.comment_ids) + 1:03d}"
        self.comments[cid] = {"document_id": doc_id, "author": user, "text": body["text"]}
        self.comment_ids.append(cid)
        self.comment_parent[cid] = doc_id
        return Resp(201, self._comment_view(cid), kind="comment")

    def _comment_lookup(self, user, doc_id, cid):
        if user not in self.users:
            return _err(401, "authentication required")
        if not self.can_read(user, doc_id):
            return _err(404, "not found")
        if cid not in self.comments or self.comments[cid]["document_id"] != doc_id:
            return _err(404, "no such comment")
        return None

    def get_comment(self, user, doc_id, cid):
        failed = self._comment_lookup(user, doc_id, cid)
        return failed or Resp(200, self._comment_view(cid), kind="comment")

    def delete_comment(self, user, doc_id, cid):
        failed = self._comment_lookup(user, doc_id, cid)
        if failed:
            return failed
        if not (self.comments[cid]["author"] == user or self.can_manage(user, doc_id)):
            return _err(403, "forbidden")
        del self.comments[cid]
        return Resp(204)
