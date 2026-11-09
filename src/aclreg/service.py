"""The HTTP service under test.

Deliberately written in a different style from model.py (route table, store
objects, a denormalised share index used by the list endpoint) so the two are
not copy-paste twins. Seeded bugs are switched on with `bug(name)`; with no
bugs enabled the service should agree with the model on every request.
"""
import base64
import binascii
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .mutations import BY_NAME


class HttpError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class DocumentService:
    def __init__(self, contract, bugs=()):
        unknown = set(bugs) - set(BY_NAME)
        if unknown:
            raise ValueError(f"unknown bugs: {sorted(unknown)}")
        self.contract = contract
        self.bugs = frozenset(bugs)
        self.lock = threading.RLock()
        self.routes = [
            ("GET", r"/v1/documents", self.list_documents),
            ("POST", r"/v1/documents", self.create_document),
            ("GET", r"/v1/documents/(?P<doc>[^/]+)", self.get_document),
            ("PATCH", r"/v1/documents/(?P<doc>[^/]+)", self.patch_document),
            ("DELETE", r"/v1/documents/(?P<doc>[^/]+)", self.delete_document),
            ("PUT", r"/v1/documents/(?P<doc>[^/]+)/shares/(?P<user>[^/]+)", self.put_share),
            ("DELETE", r"/v1/documents/(?P<doc>[^/]+)/shares/(?P<user>[^/]+)", self.delete_share),
            ("GET", r"/v1/documents/(?P<doc>[^/]+)/comments", self.list_comments),
            ("POST", r"/v1/documents/(?P<doc>[^/]+)/comments", self.post_comment),
            ("GET", r"/v1/documents/(?P<doc>[^/]+)/comments/(?P<cid>[^/]+)", self.get_comment),
            ("DELETE", r"/v1/documents/(?P<doc>[^/]+)/comments/(?P<cid>[^/]+)", self.delete_comment),
        ]
        self.routes = [(m, re.compile(p + r"\Z"), h) for m, p, h in self.routes]
        self.reset()

    def bug(self, name):
        return name in self.bugs

    def reset(self):
        with self.lock:
            c = self.contract
            self.store = {k: dict(v, id=k) for k, v in c["documents"].items()}
            self.grants = {k: dict(c.get("shares", {}).get(k, {})) for k in self.store}
            # denormalised: user -> doc ids shared with them (used by list)
            self.shared_index = {}
            for doc_id, grants in self.grants.items():
                for user in grants:
                    self.shared_index.setdefault(user, set()).add(doc_id)
            self.comment_store = {k: dict(v, id=k) for k, v in c.get("comments", {}).items()}
            self.doc_counter = len(self.store)
            self.comment_counter = len(self.comment_store)

    # ----- policy ----------------------------------------------------------
    def principal(self, user):
        info = self.contract["users"].get(user)
        if info is None:
            raise HttpError(401, "authentication required")
        return dict(info, name=user)

    def readable(self, who, doc):
        if self.bug("admin-skips-tenant") and who["role"] == "admin":
            return True
        if doc["tenant"] != who["tenant"]:
            return False
        if who["role"] == "admin" or doc["owner"] == who["name"]:
            return True
        return who["name"] in self.grants.get(doc["id"], {})

    def writable(self, who, doc):
        if who["role"] == "viewer" and not self.bug("viewer-cap-ignored"):
            return False
        if who["role"] == "admin" or doc["owner"] == who["name"]:
            return True
        return self.grants.get(doc["id"], {}).get(who["name"]) == "editor"

    def manageable(self, who, doc):
        return who["role"] == "admin" or doc["owner"] == who["name"]

    def load_readable(self, who, doc_id):
        doc = self.store.get(doc_id)
        if doc is None:
            raise HttpError(404, "not found")
        if not self.readable(who, doc):
            if self.bug("cross-tenant-403") and doc["tenant"] != who["tenant"]:
                raise HttpError(403, "forbidden")
            if self.bug("private-403") and doc["tenant"] == who["tenant"]:
                raise HttpError(403, "forbidden")
            raise HttpError(404, "not found")
        return doc

    def check_if_match(self, doc, headers, method):
        supplied = headers.get("If-Match")
        if supplied is None:
            if method == "PATCH" and self.bug("patch-if-match-optional"):
                return
            raise HttpError(428, "If-Match required")
        if supplied != self.etag(doc):
            if method == "DELETE" and self.bug("delete-ignores-stale-etag"):
                return
            raise HttpError(412, "stale version")

    @staticmethod
    def etag(doc):
        return f'"v{doc["version"]}"'

    def render(self, doc):
        out = {key: doc[key] for key in ("id", "tenant", "owner", "title", "version")}
        if self.bug("version-as-string"):
            out["version"] = str(out["version"])
        if self.bug("doc-leaks-shares"):
            out["shares"] = dict(self.grants.get(doc["id"], {}))
        return out

    def render_comment(self, comment):
        out = {key: comment[key] for key in ("id", "document_id", "author", "text")}
        if self.bug("comment-missing-document-id"):
            del out["document_id"]
        return out

    @staticmethod
    def parse_body(raw, allowed):
        try:
            data = json.loads(raw.decode("utf-8")) if raw else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise HttpError(400, "invalid json")
        if not isinstance(data, dict) or not set(data) <= set(allowed):
            raise HttpError(400, "unexpected fields")
        return data

    @staticmethod
    def require_text(data, key, limit):
        value = data.get(key)
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= limit:
            raise HttpError(400, f"invalid {key}")
        return value

    # ----- cursors ---------------------------------------------------------
    @staticmethod
    def encode_cursor(user, after):
        raw = json.dumps({"u": user, "a": after}, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @staticmethod
    def decode_cursor(token):
        try:
            padded = token + "=" * (-len(token) % 4)
            data = json.loads(base64.urlsafe_b64decode(padded.encode()))
            return str(data["u"]), str(data["a"])
        except (ValueError, KeyError, TypeError, binascii.Error):
            raise HttpError(400, "bad cursor")

    # ----- handlers --------------------------------------------------------
    def list_documents(self, who, query, headers, raw):
        try:
            limit = int(query.get("limit", ["10"])[0])
        except ValueError:
            raise HttpError(400, "bad limit")
        if not 1 <= limit <= 50:
            raise HttpError(400, "bad limit")
        after = None
        scope = who
        token = query.get("cursor", [None])[0]
        if token is not None:
            issuer, after = self.decode_cursor(token)
            if issuer != who["name"]:
                if not self.bug("cursor-not-bound"):
                    raise HttpError(400, "cursor belongs to another user")
                scope = self.principal(issuer)  # "resume the query the cursor describes"
        shared = self.shared_index.get(scope["name"], set())
        rows = []
        for doc_id in sorted(self.store):
            doc = self.store[doc_id]
            if scope["role"] == "admin" and self.bug("admin-list-all-tenants"):
                rows.append(doc)
                continue
            if token is not None and self.bug("continuation-skips-acl"):
                if doc["tenant"] == scope["tenant"]:
                    rows.append(doc)
                continue
            if self.bug("admin-skips-tenant") and scope["role"] == "admin":
                rows.append(doc)
                continue
            if doc["tenant"] != scope["tenant"]:
                continue
            if scope["role"] == "admin" or doc["owner"] == scope["name"] or doc_id in shared:
                rows.append(doc)
        if after is not None:
            if self.bug("cursor-inclusive"):
                rows = [d for d in rows if d["id"] >= after]
            else:
                rows = [d for d in rows if d["id"] > after]
        page = rows[:limit]
        if self.bug("next-cursor-when-full"):
            more = len(page) == limit
        else:
            more = len(rows) > limit
        nxt = self.encode_cursor(who["name"], page[-1]["id"]) if more and page else None
        return 200, {"items": [self.render(d) for d in page], "next_cursor": nxt}, None

    def create_document(self, who, query, headers, raw):
        if who["role"] == "viewer" and not self.bug("viewer-can-create"):
            raise HttpError(403, "viewers cannot create")
        allowed = {"title"}
        if self.bug("create-accepts-owner"):
            allowed.add("owner")
        if self.bug("create-accepts-tenant"):
            allowed.add("tenant")
        data = self.parse_body(raw, allowed)
        title = self.require_text(data, "title", 80)
        self.doc_counter += 1
        doc_id = f"d{self.doc_counter:03d}"
        doc = {"id": doc_id, "tenant": data.get("tenant", who["tenant"]),
               "owner": data.get("owner", who["name"]), "title": title, "version": 1}
        self.store[doc_id] = doc
        self.grants[doc_id] = {}
        return 201, self.render(doc), self.etag(doc)

    def get_document(self, who, query, headers, raw, doc):
        found = self.load_readable(who, doc)
        return 200, self.render(found), self.etag(found)

    def patch_document(self, who, query, headers, raw, doc):
        found = self.load_readable(who, doc)
        if not self.writable(who, found):
            raise HttpError(403, "forbidden")
        self.check_if_match(found, headers, "PATCH")
        allowed = {"title"}
        if self.bug("patch-accepts-owner"):
            allowed.add("owner")
        if self.bug("patch-accepts-version"):
            allowed.add("version")
        data = self.parse_body(raw, allowed)
        title = self.require_text(data, "title", 80)
        found["title"] = title
        if "owner" in data:
            found["owner"] = data["owner"]
        if not self.bug("patch-no-version-bump"):
            found["version"] += 1
        if "version" in data:
            found["version"] = data["version"]
        return 200, self.render(found), self.etag(found)

    def _require_manage(self, who, doc):
        if self.bug("editor-share-can-share"):
            ok = self.writable(who, doc)
        else:
            ok = self.manageable(who, doc)
        if not ok:
            raise HttpError(403, "forbidden")

    def delete_document(self, who, query, headers, raw, doc):
        found = self.load_readable(who, doc)
        ok = self.writable(who, found) if self.bug("editor-share-can-delete") else self.manageable(who, found)
        if not ok:
            raise HttpError(403, "forbidden")
        self.check_if_match(found, headers, "DELETE")
        del self.store[doc]
        for user in self.grants.pop(doc, {}):
            self.shared_index.get(user, set()).discard(doc)
        for cid in [k for k, v in self.comment_store.items() if v["document_id"] == doc]:
            del self.comment_store[cid]
        return 204, None, None

    def put_share(self, who, query, headers, raw, doc, user):
        found = self.load_readable(who, doc)
        self._require_manage(who, found)
        data = self.parse_body(raw, {"role"})
        role = data.get("role")
        if role not in ("viewer", "editor"):
            raise HttpError(400, "invalid role")
        target = self.contract["users"].get(user)
        if target is None or user == found["owner"]:
            raise HttpError(400, "invalid grantee")
        if target["tenant"] != found["tenant"] and not self.bug("share-cross-tenant"):
            raise HttpError(400, "invalid grantee")
        if self.bug("reshare-keeps-old-role"):
            self.grants[doc].setdefault(user, role)
        else:
            self.grants[doc][user] = role
        self.shared_index.setdefault(user, set()).add(doc)
        return 200, {"document_id": doc, "user": user, "role": role}, None

    def delete_share(self, who, query, headers, raw, doc, user):
        found = self.load_readable(who, doc)
        self._require_manage(who, found)
        if user not in self.grants[doc]:
            if self.bug("revoke-missing-204"):
                return 204, None, None
            raise HttpError(404, "no such share")
        del self.grants[doc][user]
        if not self.bug("revoke-stale-index"):
            self.shared_index.get(user, set()).discard(doc)
        return 204, None, None

    def list_comments(self, who, query, headers, raw, doc):
        if self.bug("comment-list-tenant-only"):
            found = self.store.get(doc)
            if found is None or found["tenant"] != who["tenant"]:
                raise HttpError(404, "not found")
        else:
            self.load_readable(who, doc)
        items = [self.render_comment(c) for k, c in sorted(self.comment_store.items())
                 if c["document_id"] == doc]
        return 200, {"items": items}, None

    def post_comment(self, who, query, headers, raw, doc):
        self.load_readable(who, doc)
        data = self.parse_body(raw, {"text"})
        text = self.require_text(data, "text", 200)
        self.comment_counter += 1
        cid = f"c{self.comment_counter:03d}"
        comment = {"id": cid, "document_id": doc, "author": who["name"], "text": text}
        self.comment_store[cid] = comment
        return 201, self.render_comment(comment), None

    def _load_comment(self, who, doc, cid, ignore_parent):
        self.load_readable(who, doc)
        comment = self.comment_store.get(cid)
        if comment is None or (comment["document_id"] != doc and not ignore_parent):
            raise HttpError(404, "no such comment")
        return comment

    def get_comment(self, who, query, headers, raw, doc, cid):
        comment = self._load_comment(who, doc, cid, self.bug("comment-get-ignores-parent"))
        return 200, self.render_comment(comment), None

    def delete_comment(self, who, query, headers, raw, doc, cid):
        comment = self._load_comment(who, doc, cid, self.bug("comment-delete-ignores-parent"))
        parent = self.store[doc]
        allowed = comment["author"] == who["name"] or self.manageable(who, parent)
        if not allowed and not self.bug("comment-delete-any-reader"):
            raise HttpError(403, "forbidden")
        del self.comment_store[cid]
        return 204, None, None

    # ----- dispatch --------------------------------------------------------
    def handle(self, method, raw_path, headers, body):
        """Returns (status, payload, etag)."""
        parts = urlsplit(raw_path)
        query = parse_qs(parts.query)
        with self.lock:
            try:
                who = self.principal(headers.get("X-User"))
                matched_path = False
                for route_method, pattern, handler in self.routes:
                    match = pattern.match(parts.path)
                    if not match:
                        continue
                    matched_path = True
                    if route_method == method:
                        return handler(who, query, headers, body, **match.groupdict())
                if matched_path:
                    raise HttpError(405, "method not allowed")
                raise HttpError(404, "not found")
            except HttpError as error:
                return error.status, {"error": error.message}, None


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive: one TCP connection per test run
    disable_nagle_algorithm = True  # headers and body are separate writes

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _dispatch(self, method):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0 or length > 65536:
            self.send_error(413)
            return
        body = self.rfile.read(length) if length else b""
        status, payload, etag = self.server.service.handle(method, self.path, self.headers, body)
        data = b"" if payload is None else json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        if etag:
            self.send_header("ETag", etag)
        self.end_headers()
        if data:
            self.wfile.write(data)

    def log_message(self, *_args):
        pass


def start_server(contract, bugs=()):
    service = DocumentService(contract, bugs)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.daemon_threads = True
    server.service = service
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    return server, thread, service


def stop_server(server, thread):
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)
