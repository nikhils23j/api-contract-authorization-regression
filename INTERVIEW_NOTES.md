# Interview notes: API authorization regression lab

## 1. What it is

A small multi-tenant document-sharing HTTP API (roles, shares, nested
comments, cursor pagination, ETags), plus a pure-Python reference model of how
it *should* behave. I seeded 30 one-line authorization and contract bugs into
the API and measured which testing strategy catches each one: a happy-path
smoke test, exhaustive single-request enumeration, or stateful random
sequences that shrink failures to a minimal story. It's a personal project,
standard library only, run on my laptop, and built with AI pair-programming
help.

## 2. How it works

**Pieces**
- `service.py` is the real HTTP server (`http.server`, keep-alive). It has a
  route table, an in-memory store, a separate "shared with me" index used by
  list, and 30 `bug(name)` switches.
- `model.py` is the same policy as plain functions over dicts. No HTTP, no
  shared code with the service. It is the oracle.
- `ops.py` defines ops as tuples like `("share", "alice", 3, "erin", "editor")`.
  Documents and comments are referenced by slot number (index into "every id
  ever issued", modulo).
- `harness.py` replays an op list from the fixture against both sides and
  compares status, body, and ETag, plus a JSON schema check. It also contains
  the shrinker and story printer.
- `strategies.py` has baseline, enumeration, stateful uniform, and stateful
  guided.
- `run_lab.py` runs the clean service and every bug × every strategy across
  seeds, in 4 processes, and writes `results/results.json` + `summary.md`.

**Decisions and why**
- **Model as oracle instead of hand-coded expected statuses.** With one oracle
  shared by every strategy, the only variable is *which requests get sent*,
  so the comparison is fair. It also lets random sequences be checked at all,
  since you can't hand-write expected results for random input.
- **404 for unreadable, 403 only for readable-but-forbidden.** This stops
  probing for whether something exists. Two of the seeded bugs break exactly
  this rule.
- **Slots instead of ids.** If a shrinker deletes a `create`, concrete ids
  would point at nothing. Slots always resolve to something. Before shrinking
  I canonicalize slots to their resolved index so that deleting unrelated
  steps doesn't retarget later ops.
- **Shrinker.** It's a delta-debugging-style deletion pass (chunks, then
  single ops), then an argument pass (retarget ops at fixture docs so `create`
  steps become removable), then deletion again. The predicate is "still
  disagrees with the model". That's safe because exactly one bug is on and the
  clean service has zero disagreements.
- **Guided generation.** Uniform random rarely builds the right state (for
  example, editor share to an editor-role user, then that user acts). Guided
  mode reads the model to pick live docs in your tenant, existing grants to
  revoke, and people involved with the doc. Ops are still plain tuples, so
  replay and shrinking are unchanged.
- **Real faults, not table cells.** v1 had "60 variants" where 40 were one
  bypass per user/doc/method cell. That flatters a table-walking test. v2 uses
  30 faults in distinct code paths.
- **Keep-alive client + 4 processes.** Without them the full evaluation would
  not have fit in a few minutes. Results are deterministic per seed regardless
  of parallelism.

## 3. Key results (measured 9 Nov 2026, Apple M2, Python 3.10.8)

- **Clean service:** 0 disagreements with the model over 3 × 1,962
  enumeration requests and 50,000 stateful requests. Same again for a second
  seed range.
- **Bugs caught (of 30):**
  - baseline: 4
  - enumeration: 24
  - stateful uniform: 28
  - stateful guided: 30
  - The stateful numbers are the union of 10 seeds, and the totals are the
    same for seeds 0–9 and 10–19.
- **Single seed (2,500 requests):** guided caught 26–30 (median 28) and
  uniform caught 24–27 (median 26), across 20 seeds.
- **The 6 bugs enumeration missed:**
  - 4 need two requests in order: revoke→list, share→write, cursor replay,
    paging past page 1 as a non-admin.
  - 2 need a state the fixture never sets up: an editor-role user with an
    editor share.
  - Guided caught these in 5/20 to 20/20 seeds each. Uniform caught two of
    them in 0/20.
  - Adding one grant to the fixture lets enumeration catch the delete one
    (there's a test for this).
- **Shrinking:** every detection shrank to 1–3 steps (median 1, from a median
  of 14).
- **Runtime:** full evaluation 104 s wall-clock on a loaded machine. A second
  run took 384 s under heavier load (about 67 s CPU).

**What they do not show**
- They don't show finding real vulnerabilities. The bugs are mine.
- They don't validate the spec itself. The model and service could share a
  misunderstanding.
- They don't say contract testing is weak. My enumeration is single-request by
  design, and a human-written scenario would catch the multi-step bugs.
- Stateful rates depend on budget, seeds, and generator weights.
- This isn't production, real auth, or a real network.

## 4. Spoken answer (~2 minutes, STAR)

**Situation**
I'd built a small contract-testing project for an API: a localhost service
and a test runner that checked every user against every document for GET,
PATCH and DELETE. It was all green, and I was pretty happy with it. Then I
started wondering about bugs that only show up between two requests. For
example, you revoke someone's access, but the document still shows up in
their list because the list reads from a different index.

**Task**
I wanted to find out, with actual numbers, which kinds of authorization bugs
each testing approach can catch. I gave myself one rule: every strategy uses
the same oracle, so the comparison is fair.

**Action**
I rebuilt the API with more realistic rules: tenants, viewer/editor/admin
roles, document sharing, comments nested under documents, cursor pagination,
and ETag-based concurrency control. Then I wrote a separate pure-Python model
of the same rules to act as the oracle. I hid 30 one-line bugs behind
switches, things like cross-tenant leaks, IDOR on nested comment paths, mass
assignment, and stale ETags. I compared four strategies:
- a happy-path smoke test,
- exhaustive single-request enumeration,
- random operation sequences, uniform,
- random operation sequences, guided by the model's state.

When a random sequence fails, a shrinker cuts it down to the shortest version
that still fails and prints it as a little numbered story. I built it with an
AI pair-programming assistant, but I designed the experiment, ran everything,
and checked every number myself.

**Result**
The clean service matched the model on about 56,000 requests. Of the 30 bugs,
the smoke test caught 4, enumeration caught 24, and guided random sequences
caught all 30 across 10 seeds. A single seed of 2,500 requests caught between
26 and 30. The six enumeration missed were all multi-step or fixture-gap bugs,
and every failure shrank to at most three steps. The part I found most
interesting is that two of those misses weren't about ordering at all: my
fixture just never had an editor holding an editor share. That taught me a
test suite is only as good as the states it can reach.

## 5. Likely follow-up questions

**Q: Isn't the model just the service written twice? What if both are wrong?**
Yes, that's the biggest limit. Same authors, so a shared misunderstanding
wouldn't show up. I kept them structurally different (the service has a
denormalized share index and a route table; the model is plain functions),
and there's a separate schema check, but that only catches certain kinds of
mismatch. Ideally someone else would write the model from a written spec.

**Q: Why not just use Hypothesis?**
I wanted to stay standard-library only and understand the pieces: generation,
replay, shrinking. Hypothesis's stateful testing with bundles and its shrinker
is more powerful. My guided mode is basically a hand-rolled version of
drawing from bundles.

**Q: Your enumeration is set up to lose. Is the comparison fair?**
It's single-request by definition, so yes, it can't see ordering bugs. I say
that explicitly. A good engineer would add a "revoke then list" scenario. The
point is that random sequences found those without anyone thinking of the
scenario. Also, 2 of its 6 misses were plain fixture gaps, which I think is a
more useful lesson than the ordering ones.

**Q: How do you know the shrunk story is the same bug?**
Only one bug switch is on per run, and the clean service has zero
disagreements with the model over every request I sent. So any disagreement
comes from that switch. The shrinker is greedy, though, so the result is
locally minimal (you can't remove any single step), not guaranteed to be the
shortest possible story.

**Q: Why 404 instead of 403 for documents you can't see?**
A 403 confirms the document exists, which leaks information across tenants.
403 is only for "you can see it but can't do this". Two of the seeded bugs
break exactly this rule, and enumeration catches both.

**Q: Some bugs were only caught in 5 of 20 seeds. Is that good enough?**
Not for those bugs at that budget. They need a specific setup: an editor-role
user given an editor share, who then deletes or writes. More budget or better
guidance would help. I report the per-seed spread instead of just the union
so that's visible.

**Q: What's an ETag / If-Match doing here?**
It's optimistic concurrency. GET returns `ETag: "v3"`, and PATCH/DELETE must
send `If-Match: "v3"`. If the document changed since then, you get 412, and
if you leave it out, 428. That prevents lost updates. The seeded bugs make it
optional, ignore stale tags, or never bump the version.

**Q: What would you do next?**
- Have the model written independently from a prose spec.
- Make the generator coverage-driven, so it aims at policy branches it hasn't
  hit yet.
- Run the same harness against a real framework app, for example a small
  Flask or Django app, to see if the approach transfers.

## 6. Things I should be able to explain cold

- [ ] The read/write/manage rules, including the viewer cap on editor shares.
- [ ] Why unreadable is 404 and not 403.
- [ ] How cursors work and why they're tied to a user. What the
      `cursor-not-bound` bug does.
- [ ] What IDOR on a nested path means (`/documents/d001/comments/c003`, where
      c003 belongs to d002).
- [ ] Mass assignment, and why strict request bodies prevent it.
- [ ] ETag / If-Match, plus the difference between 412 and 428.
- [ ] Why one shared oracle makes the strategy comparison fair.
- [ ] What a slot is, why ops use slots, and what canonicalization fixes.
- [ ] The shrinker's three passes, and why "still fails" is a safe predicate
      here.
- [ ] Uniform vs guided generation, and one case where guided wasn't better
      (`cursor-not-bound`).
- [ ] Every headline number: 0 clean mismatches; 4 / 24 / 28 / 30; single
      seed 26–30; shrunk 1–3 steps. Also what each one does *not* show.
- [ ] The `revoke-stale-index` story from memory.
- [ ] Which parts the AI assistant helped with, and that I verified every
      number by re-running.
