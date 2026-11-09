"""Run op sequences against the real HTTP service and the model side by side."""
from dataclasses import dataclass, field
from typing import List, Optional

from .client import Client
from .contract import schema_problems
from .model import Model
from .ops import resolve
from .service import start_server, stop_server


@dataclass
class Outcome:
    requests: int                      # HTTP requests sent in this run
    failed_at: Optional[int] = None    # index of first mismatching op
    problems: List[str] = field(default_factory=list)
    trace: List[tuple] = field(default_factory=list)  # (text, service_status, model_status)
    ops: List[tuple] = field(default_factory=list)     # ops drawn in generated mode

    @property
    def failed(self):
        return self.failed_at is not None


def compare(contract, step, got, want):
    status, body, etag = got
    problems = []
    if status != want.status:
        problems.append(f"status {status}, model says {want.status}")
        return problems
    if want.status >= 400:
        if not isinstance(body, dict) or set(body) != {"error"}:
            problems.append(f"error body {body!r} is not {{'error': ...}}")
        return problems
    problems += schema_problems(contract, want.kind, body)
    if want.kind == "list" and isinstance(body, dict):
        if body.get("items") != want.body["items"]:
            got_ids = [i.get("id") for i in body.get("items", []) if isinstance(i, dict)]
            want_ids = [i["id"] for i in want.body["items"]]
            problems.append(f"items {got_ids}, model says {want_ids}"
                            if got_ids != want_ids else "list item contents differ from model")
        if (body.get("next_cursor") is None) != (want.body["next_cursor"] is None):
            problems.append(f"next_cursor present={body.get('next_cursor') is not None}, "
                            f"model says {want.body['next_cursor'] is not None}")
    elif body != want.body:
        problems.append(f"body {body!r}, model says {want.body!r}")
    if etag != want.etag:
        problems.append(f"ETag {etag!r}, model says {want.etag!r}")
    return problems


class Harness:
    """One server per mutant; reset between sequences. Use as a context manager."""

    def __init__(self, contract, bugs=()):
        self.contract = contract
        self.server, self.thread, self.service = start_server(contract, bugs)
        self.client = Client(self.server.server_port)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self.client.close()
        stop_server(self.server, self.thread)

    def run(self, ops, record=False, generate=None):
        """Replay ops from the fixture state; stop at the first disagreement.

        If `generate` is given (a function model -> op), `ops` is the number of
        ops to draw on the fly, and the drawn ops are stored in outcome.ops.
        """
        self.service.reset()
        model = Model(self.contract)
        cursors = {}
        outcome = Outcome(0)
        if generate is not None:
            count, ops = ops, []
            source = iter(lambda: generate(model), None)
            source = (op for _, op in zip(range(count), source))
        else:
            source = ops
        for index, op in enumerate(source):
            if generate is not None:
                ops.append(op)
                outcome.ops = ops
            step = resolve(op, model, cursors)
            got = self.client.request(step.method, step.path, step.user, step.body, step.if_match)
            outcome.requests += 1
            want = step.expect()
            problems = compare(self.contract, step, got, want)
            if record or problems:
                outcome.trace.append((step.text, got[0], want.status))
            if problems:
                outcome.failed_at = index
                outcome.problems = problems
                return outcome
            if step.list_actor is not None:
                svc_next = got[1].get("next_cursor") if isinstance(got[1], dict) else None
                if svc_next and want.next_cursor:
                    cursors[step.list_actor] = (svc_next, want.next_cursor)
                else:
                    cursors.pop(step.list_actor, None)
        return outcome

    def shrink(self, ops):
        """Delta-debugging style: drop chunks, then single ops, while it still fails.

        With a single seeded bug and a clean service that agrees with the model,
        "still fails" is a safe test that we are chasing the same defect.
        Returns (minimal_ops, replays_used).
        """
        replays = 0

        def fails(candidate):
            nonlocal replays
            replays += 1
            return self.run(candidate).failed

        first = self.run(ops)
        replays += 1
        if not first.failed:
            return list(ops), replays
        current = self.canonical(ops[: first.failed_at + 1])
        if not fails(current):  # canonical form is equivalent by construction; be defensive
            current = list(ops[: first.failed_at + 1])
        current = self._delete_pass(current, fails)
        # argument pass: point ops at fixture documents (slots 0-4) where possible,
        # which often makes earlier `create` steps unnecessary; then delete again
        simplified = False
        for index, op in enumerate(current):
            slot = op[2] if len(op) > 2 else None
            if op[0] == "list" or not isinstance(slot, int) or slot < 5:
                continue
            for fixture_slot in range(5):
                candidate = current[:index] + [op[:2] + (fixture_slot,) + op[3:]] + current[index + 1:]
                if fails(candidate):
                    current, simplified = candidate, True
                    break
        if simplified:
            current = self._delete_pass(current, fails)
        # a failure might now happen earlier than the last op: trim the tail
        final = self.run(current)
        replays += 1
        if final.failed:
            current = current[: final.failed_at + 1]
        return current, replays

    @staticmethod
    def _delete_pass(current, fails):
        chunks = 2
        while len(current) >= 2:
            size = max(1, len(current) // chunks)
            reduced = False
            for start in range(0, len(current), size):
                candidate = current[:start] + current[start + size:]
                if candidate and fails(candidate):
                    current = candidate
                    chunks = max(chunks - 1, 2)
                    reduced = True
                    break
            if not reduced:
                if size == 1:
                    break
                chunks = min(chunks * 2, len(current))
        return current

    def canonical(self, ops):
        """Rewrite slot numbers as the index they resolve to at that point.

        Slots are taken modulo the number of ids issued so far, so deleting an
        earlier `create` would silently retarget later ops. After this rewrite
        a slot keeps pointing at the same document/comment when unrelated
        steps are removed, which lets the shrinker drop them.
        """
        model = Model(self.contract)
        out = []
        for op in ops:
            op = list(op)
            if op[0] in ("get", "comments", "patch", "delete", "share", "revoke", "comment"):
                op[2] %= len(model.doc_ids)
            elif op[0] in ("get_comment", "delete_comment"):
                if op[2] is not None:
                    op[2] %= len(model.doc_ids)
                if model.comment_ids:
                    op[3] %= len(model.comment_ids)
            op = tuple(op)
            out.append(op)
            resolve(op, model, {}).expect()  # advance the model only
        return out

    def story(self, ops):
        """Human-readable numbered repro of a (shrunk) failing sequence."""
        outcome = self.run(ops, record=True)
        lines = []
        for number, (text, got, want) in enumerate(outcome.trace, 1):
            last = outcome.failed and number == len(outcome.trace)
            mark = "   <-- service disagrees with model" if last else ""
            lines.append(f"{number}. {text} -> {got}{mark}")
        if outcome.failed:
            lines.append("   " + "; ".join(outcome.problems))
        return lines
