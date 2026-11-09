import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aclreg.contract import expected_status, load_contract
from aclreg.mutations import all_mutations
from aclreg.runner import request, run_suite
from aclreg.service import start_server


class ContractLabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = load_contract()

    def test_oracle_preserves_tenant_and_owner_boundaries(self):
        self.assertEqual(expected_status(self.contract, "alice", "a1", "PATCH"), 200)
        self.assertEqual(expected_status(self.contract, "erin", "a1", "PATCH"), 403)
        self.assertEqual(expected_status(self.contract, "carol", "a1", "GET"), 404)
        self.assertEqual(expected_status(self.contract, None, "a1", "GET"), 401)

    def test_actual_http_rejects_cross_tenant_access_and_stale_write(self):
        server, thread, service = start_server(self.contract)
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            self.assertEqual(request(base, "GET", "/v1/documents/a1", "carol")[0], 404)
            self.assertEqual(
                request(base, "PATCH", "/v1/documents/a1", "erin", {"title": "No"}, 1)[0],
                403,
            )
            self.assertEqual(
                request(base, "PATCH", "/v1/documents/a1", "alice", {"title": "Stale"}, 0)[0],
                412,
            )
            self.assertEqual(request(base, "GET", "/v1/documents/a1", "alice")[1]["version"], 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_clean_full_suite(self):
        result = run_suite(self.contract)
        self.assertGreater(result["checks"], 200)
        self.assertEqual(result["failures"], [])

    def test_auth_schema_list_and_revision_mutations_are_detected(self):
        mutations = all_mutations(self.contract)
        for kind in ("auth", "schema", "list", "revision"):
            with self.subTest(kind=kind):
                mutation = next(item for item in mutations if item.kind == kind)
                result = run_suite(self.contract, mutation)
                self.assertTrue(result["failures"], mutation.name)


if __name__ == "__main__":
    unittest.main()
