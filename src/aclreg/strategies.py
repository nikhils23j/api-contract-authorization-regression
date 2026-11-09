"""Three ways of choosing requests. All share the same oracle (the model).

* baseline     - one owner happy-path script (what a quick smoke test looks like)
* enumeration  - every single request from the fixture state: each user x each
                 document x each endpoint, plus stale/missing ETags, extra-field
                 bodies, and paging through lists. Reset between cases.
* stateful     - seeded random op sequences (uniform or model-guided arguments);
                 on failure, shrink to a minimal story.
"""
import random

from .ops import USERS, random_op

FIXTURE_DOC_SLOTS = range(5)      # d001..d005
FIXTURE_COMMENT_SLOTS = range(3)  # c001..c003


def baseline_cases():
    # slot 5 = the document alice creates, slot 3 = the comment she adds
    return [[
        ("create", "alice", "ok"),
        ("get", "alice", 5),
        ("patch", "alice", 5, "current", "ok"),
        ("list", "alice", 50, None),
        ("comment", "alice", 5, "ok"),
        ("get_comment", "alice", None, 3),
        ("comments", "alice", 5),
        ("delete_comment", "alice", None, 3),
        ("delete", "alice", 5, "current"),
    ]]


def enumeration_cases():
    cases = []
    for actor in USERS + [None]:
        cases.append([("list", actor, 2, None), ("list", actor, 2, "self"), ("list", actor, 2, "self")])
        cases.append([("list", actor, 50, None)])
        for kind in ("ok", "extra_owner", "extra_tenant", "bad"):
            cases.append([("create", actor, kind)])
        for d in FIXTURE_DOC_SLOTS:
            cases.append([("get", actor, d)])
            for tag, body in (("current", "ok"), ("stale", "ok"), ("missing", "ok"),
                              ("current", "extra_owner"), ("current", "extra_version"),
                              ("current", "bad")):
                cases.append([("patch", actor, d, tag, body)])
            for tag in ("current", "stale", "missing"):
                cases.append([("delete", actor, d, tag)])
            cases.append([("comments", actor, d)])
            cases.append([("comment", actor, d, "ok")])
            for grantee in USERS:
                for role in ("viewer", "editor"):
                    cases.append([("share", actor, d, grantee, role)])
                cases.append([("revoke", actor, d, grantee)])
            for c in FIXTURE_COMMENT_SLOTS:
                cases.append([("get_comment", actor, d, c)])
                cases.append([("delete_comment", actor, d, c)])
    return cases


def run_cases(harness, cases):
    """Run scripted cases until the first disagreement.

    Returns dict(detected, requests, requests_to_detection, failing_case, problems).
    """
    sent = 0
    for case in cases:
        outcome = harness.run(case)
        sent += outcome.requests
        if outcome.failed:
            return {"detected": True, "requests": sent, "requests_to_detection": sent,
                    "failing_case": case[: outcome.failed_at + 1], "problems": outcome.problems}
    return {"detected": False, "requests": sent, "requests_to_detection": None,
            "failing_case": None, "problems": []}


def run_stateful(harness, seed, budget, length, guided=False):
    """Random sequences of `length` ops (fresh state each) until `budget` ops are spent.

    guided=False: uniform random arguments.
    guided=True:  the generator peeks at the model to pick plausible arguments.
    """
    rng = random.Random(seed)
    sent = 0
    while sent < budget:
        count = min(length, budget - sent)
        if guided:
            outcome = harness.run(count, generate=lambda model: random_op(rng, model))
            ops = outcome.ops
        else:
            ops = [random_op(rng) for _ in range(count)]
            outcome = harness.run(ops)
        sent += outcome.requests
        if outcome.failed:
            return {"detected": True, "requests": sent, "requests_to_detection": sent,
                    "failing_case": ops[: outcome.failed_at + 1], "problems": outcome.problems}
    return {"detected": False, "requests": sent, "requests_to_detection": None,
            "failing_case": None, "problems": []}
