import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aclreg.client import Client  # noqa: E402
from aclreg.contract import load_contract, matches_schema  # noqa: E402
from aclreg.harness import Harness  # noqa: E402
from aclreg.model import Model  # noqa: E402
from aclreg.mutations import CLASSES, all_mutations  # noqa: E402
from aclreg.service import DocumentService, start_server, stop_server  # noqa: E402
from aclreg.strategies import (  # noqa: E402
    baseline_cases, enumeration_cases, run_cases, run_stateful)


class ModelPolicyTests(unittest.TestCase):
    def setUp(self):
        self.model = Model(load_contract())

    def test_read_write_manage_rules(self):
        m = self.model
        self.assertTrue(m.can_manage("alice", "d001"))          # owner
        self.assertTrue(m.can_read("bob", "d001"))              # viewer share
        self.assertFalse(m.can_write("bob", "d001"))
        self.assertTrue(m.can_read("erin", "d001"))             # editor share...
        self.assertFalse(m.can_write("erin", "d001"))           # ...capped by viewer role
        self.assertTrue(m.can_write("amy", "d002"))             # tenant admin
        self.assertFalse(m.can_read("ben", "d001"))             # admin of the other tenant
        self.assertFalse(m.can_read("erin", "d002"))            # same tenant, not shared

    def test_unreadable_is_404_and_readable_but_forbidden_is_403(self):
        self.assertEqual(self.model.get_doc("carol", "d001").status, 404)
        self.assertEqual(self.model.get_doc("erin", "d002").status, 404)
        self.assertEqual(self.model.patch_doc("bob", "d001", {"title": "x"}, '"v1"').status, 403)
        self.assertEqual(self.model.get_doc(None, "d001").status, 401)

    def test_preconditions_and_strict_bodies(self):
        m = self.model
        self.assertEqual(m.patch_doc("alice", "d001", {"title": "x"}, None).status, 428)
        self.assertEqual(m.patch_doc("alice", "d001", {"title": "x"}, '"v0"').status, 412)
        self.assertEqual(m.patch_doc("alice", "d001", {"title": "x", "owner": "bob"}, '"v1"').status, 400)
        ok = m.patch_doc("alice", "d001", {"title": "x"}, '"v1"')
        self.assertEqual((ok.status, ok.etag, ok.body["version"]), (200, '"v2"', 2))
        self.assertEqual(m.create_doc("alice", {"title": "t", "tenant": "beta"}).status, 400)

    def test_cursor_paging_and_binding(self):
        first = self.model.list_docs("amy", 2)
        self.assertEqual([d["id"] for d in first.body["items"]], ["d001", "d002"])
        second = self.model.list_docs("amy", 2, first.next_cursor)
        self.assertEqual([d["id"] for d in second.body["items"]], ["d003"])
        self.assertIsNone(second.next_cursor)
        self.assertEqual(self.model.list_docs("alice", 2, first.next_cursor).status, 400)

    def test_nested_comment_must_belong_to_path_document(self):
        self.assertEqual(self.model.get_comment("bob", "d001", "c001").status, 200)
        self.assertEqual(self.model.get_comment("bob", "d001", "c003").status, 404)

    def test_revoke_removes_visibility(self):
        self.assertEqual(self.model.revoke("alice", "d001", "erin").status, 204)
        self.assertEqual(self.model.list_docs("erin", 50).body["items"], [])
        self.assertEqual(self.model.revoke("alice", "d001", "erin").status, 404)


class HttpServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = load_contract()

    def test_real_http_status_headers_and_schema(self):
        server, thread, _ = start_server(self.contract)
        client = Client(server.server_port)
        try:
            status, body, etag = client.request("GET", "/v1/documents/d001", "alice")
            self.assertEqual((status, etag), (200, '"v1"'))
            self.assertTrue(matches_schema(self.contract, "document", body))
            self.assertEqual(client.request("GET", "/v1/documents/d001", "carol")[0], 404)
            self.assertEqual(client.request("GET", "/v1/documents/d001")[0], 401)
            self.assertEqual(client.request("PATCH", "/v1/documents/d001", "alice", {"title": "x"})[0], 428)
            self.assertEqual(client.request("PATCH", "/v1/documents/d001", "alice", {"title": "x"}, '"v0"')[0], 412)
            self.assertEqual(client.request("POST", "/v1/documents", "alice",
                                            {"title": "x", "owner": "bob"})[0], 400)
            self.assertEqual(client.request("PUT", "/v1/documents/d001/shares/carol", "alice",
                                            {"role": "viewer"})[0], 400)
            self.assertEqual(client.request("GET", "/v1/documents?cursor=%%%", "alice")[0], 400)
            self.assertEqual(client.request("POST", "/v1/documents/d001", "alice", {"title": "x"})[0], 405)
        finally:
            client.close()
            stop_server(server, thread)

    def test_unknown_bug_name_is_rejected(self):
        with self.assertRaises(ValueError):
            DocumentService(self.contract, ["not-a-bug"])

    def test_clean_service_agrees_with_model_under_every_strategy(self):
        with Harness(self.contract) as harness:
            self.assertFalse(run_cases(harness, baseline_cases())["detected"])
            enum = run_cases(harness, enumeration_cases())
            self.assertFalse(enum["detected"], enum["problems"])
            self.assertGreater(enum["requests"], 1500)
            for seed in (0, 1):
                for guided in (False, True):
                    result = run_stateful(harness, seed, 400, 25, guided=guided)
                    self.assertFalse(result["detected"], (seed, guided, result["problems"]))
                    self.assertEqual(result["requests"], 400)


class MutationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = load_contract()

    def test_catalogue_shape(self):
        mutations = all_mutations()
        self.assertEqual(len(mutations), 30)
        self.assertEqual(len(CLASSES), 9)

    def test_every_class_has_a_bug_enumeration_catches(self):
        cases = enumeration_cases()
        single_step = {"share-lifecycle": "revoke-missing-204", "pagination": "cursor-inclusive"}
        for bug_class in CLASSES:
            first = next(m.name for m in all_mutations() if m.bug_class == bug_class)
            names = [single_step.get(bug_class, first)]
            with self.subTest(bug_class=bug_class), Harness(self.contract, names) as harness:
                self.assertTrue(run_cases(harness, cases)["detected"])

    def test_stale_share_index_needs_two_steps(self):
        with Harness(self.contract, ["revoke-stale-index"]) as harness:
            self.assertFalse(run_cases(harness, enumeration_cases())["detected"])
            outcome = harness.run([("revoke", "alice", 0, "erin"), ("list", "erin", 50, None)])
            self.assertTrue(outcome.failed)
            self.assertEqual(outcome.failed_at, 1)

    def test_fixture_gap_one_extra_grant_lets_enumeration_catch_delete_bug(self):
        """The shipped fixture has no editor-role user holding an editor share,
        so 'every user x every document' never exercises that combination."""
        with Harness(self.contract, ["editor-share-can-delete"]) as harness:
            self.assertFalse(run_cases(harness, enumeration_cases())["detected"])
        richer = copy.deepcopy(self.contract)
        richer["shares"]["d003"] = {"bob": "editor"}
        with Harness(richer, ["editor-share-can-delete"]) as harness:
            self.assertTrue(run_cases(harness, enumeration_cases())["detected"])
        with Harness(richer) as harness:  # and the richer fixture is still clean
            self.assertFalse(run_cases(harness, enumeration_cases())["detected"])

    def test_stateful_runs_are_deterministic_per_seed(self):
        with Harness(self.contract, ["cursor-inclusive"]) as harness:
            first = run_stateful(harness, 3, 2500, 25, guided=True)
            second = run_stateful(harness, 3, 2500, 25, guided=True)
        self.assertTrue(first["detected"])
        self.assertEqual(first["requests_to_detection"], second["requests_to_detection"])
        self.assertEqual(first["failing_case"], second["failing_case"])


class ShrinkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = load_contract()

    def test_shrinker_removes_noise_and_tells_a_story(self):
        noise = [("get", "amy", 1), ("create", "bob", "ok"), ("comments", "dave", 3),
                 ("list", "carol", 2, None), ("get_comment", "bob", None, 0)]
        ops = noise + [("revoke", "alice", 0, "erin")] + noise + [("list", "erin", 50, None)] + noise
        with Harness(self.contract, ["revoke-stale-index"]) as harness:
            minimal, replays = harness.shrink(ops)
            self.assertEqual(minimal, [("revoke", "alice", 0, "erin"), ("list", "erin", 50, None)])
            self.assertGreater(replays, 1)
            story = harness.story(minimal)
        self.assertEqual(story[0], "1. alice revokes erin's share on d001 -> 204")
        self.assertIn("<-- service disagrees with model", story[1])
        self.assertIn("items ['d001'], model says []", story[2])

    def test_argument_pass_drops_unneeded_create(self):
        ops = [("create", "dave", "ok"), ("patch", "ben", 5, "missing", "ok")]
        with Harness(self.contract, ["patch-if-match-optional"]) as harness:
            minimal, _ = harness.shrink(ops)
        self.assertEqual(len(minimal), 1)
        self.assertEqual(minimal[0][0], "patch")

    def test_canonical_form_is_equivalent(self):
        ops = [("create", "alice", "ok"), ("get", "alice", 1007), ("comment", "bob", 7, "ok"),
               ("get_comment", "bob", None, 1003)]
        # after the create there are 6 documents (1007 % 6 == 5); after the
        # comment there are 4 comments (1003 % 4 == 3)
        with Harness(self.contract) as harness:
            canonical = harness.canonical(ops)
            self.assertEqual(canonical[1], ("get", "alice", 5))
            self.assertEqual(canonical[3], ("get_comment", "bob", None, 3))
            self.assertFalse(harness.run(canonical).failed)


if __name__ == "__main__":
    unittest.main()
