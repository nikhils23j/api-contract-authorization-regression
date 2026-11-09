import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


class DocumentService:
    """Small HTTP service under test. Policy logic is separate from the oracle."""

    def __init__(self, contract, mutation=None):
        self.contract = contract
        self.mutation = mutation
        self.lock = threading.RLock()
        self.reset()

    def reset(self):
        with self.lock:
            self.documents = {
                document_id: dict(document, id=document_id)
                for document_id, document in self.contract["documents"].items()
            }
            self.next_id = 1

    @staticmethod
    def error(status, message):
        return status, {"error": message}

    def _principal(self, user):
        return self.contract["users"].get(user)

    def _allowed(self, user, document, method):
        principal = self._principal(user)
        if principal is None:
            return 401
        if principal["tenant"] != document["tenant"]:
            status = 404
        elif method == "GET" or principal["role"] == "admin" or document["owner"] == user:
            status = 200
        else:
            status = 403
        mutation = self.mutation
        if (
            mutation is not None
            and mutation.kind == "auth"
            and mutation.user == user
            and mutation.document_id == document["id"]
            and mutation.method == method
            and status in (403, 404)
        ):
            return 200
        return status

    def handle(self, method, raw_path, headers, body):
        path = urlsplit(raw_path).path
        user = headers.get("X-User")
        principal = self._principal(user)
        if principal is None:
            return self.error(401, "authentication required")
        with self.lock:
            if path == "/v1/documents":
                if method == "GET":
                    tenant = principal["tenant"]
                    items = [
                        self._public(doc)
                        for doc in self.documents.values()
                        if doc["tenant"] == tenant
                    ]
                    mutation = self.mutation
                    if mutation is not None and mutation.kind == "list" and mutation.user == user:
                        leaked = next(doc for doc in self.documents.values() if doc["tenant"] != tenant)
                        items.append(self._public(leaked))
                    return 200, {"documents": sorted(items, key=lambda item: item["id"])}
                if method == "POST":
                    if principal["role"] == "reader":
                        return self.error(403, "write forbidden")
                    data = self._parse_json(body)
                    if data is None or set(data) != {"title"} or not self._valid_title(data["title"]):
                        return self.error(400, "invalid title")
                    document_id = f"new{self.next_id}"
                    self.next_id += 1
                    document = {
                        "id": document_id,
                        "tenant": principal["tenant"],
                        "owner": user,
                        "title": data["title"],
                        "version": 1,
                    }
                    self.documents[document_id] = document
                    return 201, self._public(document)
                return self.error(405, "method not allowed")

            prefix = "/v1/documents/"
            if not path.startswith(prefix) or "/" in path[len(prefix):]:
                return self.error(404, "not found")
            document_id = path[len(prefix):]
            document = self.documents.get(document_id)
            if document is None:
                return self.error(404, "not found")
            if method not in ("GET", "PATCH", "DELETE"):
                return self.error(405, "method not allowed")
            status = self._allowed(user, document, method)
            if status != 200:
                return self.error(status, "access denied")
            if method == "GET":
                result = self._public(document)
                mutation = self.mutation
                if (
                    mutation is not None
                    and mutation.kind == "schema"
                    and mutation.document_id == document_id
                ):
                    result.pop(mutation.field)
                return 200, result

            supplied_version = headers.get("If-Match")
            if supplied_version is None:
                return self.error(428, "If-Match required")
            try:
                version = int(supplied_version)
            except ValueError:
                return self.error(400, "invalid If-Match")
            mutation = self.mutation
            bypass_revision = (
                mutation is not None
                and mutation.kind == "revision"
                and mutation.user == user
                and mutation.document_id == document_id
            )
            if version != document["version"] and not bypass_revision:
                return self.error(412, "stale version")
            if method == "DELETE":
                del self.documents[document_id]
                return 204, None
            data = self._parse_json(body)
            if data is None or set(data) != {"title"} or not self._valid_title(data["title"]):
                return self.error(400, "invalid title")
            document["title"] = data["title"]
            document["version"] += 1
            return 200, self._public(document)

    @staticmethod
    def _public(document):
        return dict(document)

    @staticmethod
    def _valid_title(value):
        return isinstance(value, str) and 1 <= len(value.strip()) <= 80

    @staticmethod
    def _parse_json(body):
        try:
            result = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return result if isinstance(result, dict) else None


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

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
        status, payload = self.server.service.handle(method, self.path, self.headers, body)
        data = b"" if payload is None else json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if data:
            self.wfile.write(data)

    def log_message(self, *_args):
        pass


def start_server(contract, mutation=None):
    service = DocumentService(contract, mutation)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.service = service
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    return server, thread, service
