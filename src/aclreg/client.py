"""Tiny keep-alive JSON client (http.client) so thousands of requests stay fast."""
import http.client
import json


class Client:
    def __init__(self, port):
        self.port = port
        self.conn = None
        self.sent = 0

    def _connect(self):
        self.conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)

    def request(self, method, path, user=None, body=None, if_match=None):
        headers = {}
        if user is not None:
            headers["X-User"] = user
        if if_match is not None:
            headers["If-Match"] = if_match
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        for attempt in (1, 2):
            if self.conn is None:
                self._connect()
            try:
                self.conn.request(method, path, body=data, headers=headers)
                response = self.conn.getresponse()
                raw = response.read()
                break
            except (http.client.HTTPException, ConnectionError, OSError):
                self.close()
                if attempt == 2:
                    raise
        self.sent += 1
        payload = json.loads(raw) if raw else None
        return response.status, payload, response.getheader("ETag")

    def close(self):
        if self.conn is not None:
            self.conn.close()
            self.conn = None
