import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .contract import expected_list_ids, expected_status, validate_document
from .service import start_server


def request(base, method, path, user=None, data=None, version=None):
    headers = {}
    if user is not None:
        headers["X-User"] = user
    if version is not None:
        headers["If-Match"] = str(version)
    body = None if data is None else json.dumps(data).encode("utf-8")
    if body is not None:
        headers["Content-Type"] = "application/json"
    outgoing = Request(base + path, data=body, headers=headers, method=method)
    try:
        response = urlopen(outgoing, timeout=3)
    except HTTPError as error:
        response = error
    with response:
        raw = response.read()
        return response.status, json.loads(raw) if raw else None


def run_suite(contract, mutation=None, naive=False, stop_on_first=True):
    """Exercise actual localhost HTTP, comparing responses to a separate contract."""
    server, thread, service = start_server(contract, mutation)
    base = f"http://127.0.0.1:{server.server_port}"
    checked = 0
    failures = []

    def check(label, observed, expected):
        nonlocal checked
        checked += 1
        if observed != expected:
            failures.append(f"{label}: got {observed!r}, expected {expected!r}")
        return bool(failures) and stop_on_first

    try:
        if naive:
            service.reset()
            status, payload = request(base, "GET", "/v1/documents/a1", "alice")
            if not check("happy-path GET status", status, 200):
                check("happy-path GET schema", validate_document(contract, payload), True)
            return {"checks": checked, "failures": failures}

        for user in [None] + sorted(contract["users"]):
            for document_id in sorted(contract["documents"]):
                for method in ("GET", "PATCH", "DELETE"):
                    service.reset()
                    body = {"title": "Revised"} if method == "PATCH" else None
                    version = 1 if method in ("PATCH", "DELETE") else None
                    status, payload = request(
                        base, method, f"/v1/documents/{document_id}", user, body, version
                    )
                    expected = expected_status(contract, user, document_id, method)
                    label = f"{user or 'anonymous'} {method} {document_id}"
                    if check(label + " status", status, expected):
                        return {"checks": checked, "failures": failures}
                    if status in (200, 201):
                        if check(label + " schema", validate_document(contract, payload), True):
                            return {"checks": checked, "failures": failures}
                        if method == "GET":
                            original = dict(contract["documents"][document_id], id=document_id)
                            if check(label + " contents", payload, original):
                                return {"checks": checked, "failures": failures}
                        elif method == "PATCH":
                            original = dict(contract["documents"][document_id], id=document_id)
                            original.update(title="Revised", version=2)
                            if check(label + " contents", payload, original):
                                return {"checks": checked, "failures": failures}
                    if status in (401, 403, 404):
                        if check(label + " denied-body", set(payload or {}), {"error"}):
                            return {"checks": checked, "failures": failures}
                    if method == "DELETE" and status == 204:
                        if check(label + " empty-body", payload, None):
                            return {"checks": checked, "failures": failures}

        for user in sorted(contract["users"]):
            service.reset()
            status, payload = request(base, "GET", "/v1/documents", user)
            if check(f"{user} list status", status, 200):
                return {"checks": checked, "failures": failures}
            if check(f"{user} list shape", set(payload or {}), {"documents"}):
                return {"checks": checked, "failures": failures}
            ids = sorted(item.get("id") for item in payload["documents"])
            if check(f"{user} list tenant-filter", ids, expected_list_ids(contract, user)):
                return {"checks": checked, "failures": failures}
            for item in payload["documents"]:
                if check(f"{user} list item schema", validate_document(contract, item), True):
                    return {"checks": checked, "failures": failures}

        for user in [None] + sorted(contract["users"]):
            service.reset()
            status, payload = request(base, "POST", "/v1/documents", user, {"title": "New"})
            expected = 401 if user is None else (403 if contract["users"][user]["role"] == "reader" else 201)
            if check(f"{user or 'anonymous'} create status", status, expected):
                return {"checks": checked, "failures": failures}
            if expected == 201:
                if check(f"{user} create schema", validate_document(contract, payload), True):
                    return {"checks": checked, "failures": failures}
                if check(f"{user} create owner", payload["owner"], user):
                    return {"checks": checked, "failures": failures}
                if check(f"{user} create tenant", payload["tenant"], contract["users"][user]["tenant"]):
                    return {"checks": checked, "failures": failures}

        for document_id, owner in (("a1", "alice"), ("b1", "carol")):
            service.reset()
            status, _ = request(
                base, "PATCH", f"/v1/documents/{document_id}", owner,
                {"title": "Stale"}, version=0,
            )
            if check(f"{document_id} stale write", status, 412):
                return {"checks": checked, "failures": failures}
            status, _ = request(
                base, "PATCH", f"/v1/documents/{document_id}", owner,
                {"title": "Missing revision"},
            )
            if check(f"{document_id} missing If-Match", status, 428):
                return {"checks": checked, "failures": failures}

        service.reset()
        for invalid in ("", "x" * 81, 12, None):
            status, _ = request(base, "POST", "/v1/documents", "alice", {"title": invalid})
            if check(f"invalid create title {invalid!r}", status, 400):
                return {"checks": checked, "failures": failures}
        return {"checks": checked, "failures": failures}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
