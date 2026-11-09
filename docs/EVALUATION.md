# Evaluation: 9 November 2026

The numbers here are copied from `results/results.json` (seeds 0–9) and
`results/seeds-10-19/results.json`. The generated tables are in the
`summary.md` file next to each.

## Environment and commands

- Apple M2, macOS 27.2 (arm64), Python 3.10.8, standard library only.
- The machine was shared with other heavy jobs during the runs (load average
  at start was about 104 for the first run). Wall-clock times are noisy for
  that reason. Detection results don't depend on timing.

```sh
python3 -m unittest discover -s tests -v                    # 17 tests, OK
python3 run_lab.py                                          # seeds 0-9   -> results/
python3 run_lab.py --seed-start 10 --out results/seeds-10-19  # seeds 10-19
```

Config for both runs: 2,500 requests per stateful seed, 25 ops per sequence
(state is reset between sequences), 4 worker processes (one bug per process),
and 1,944 enumeration cases (1,962 requests).

## Method

- **Oracle.** `model.py` is a pure state machine of the intended policy. For
  every request the harness compares status code, body (for lists: item list
  plus whether `next_cursor` is present), and `ETag` against the model. It
  also checks 2xx bodies against the JSON field/type schemas in
  `contracts/documents.json`.
- **Bugs.** There are 30 one-line faults behind `bug(name)` switches in
  `service.py`, and each run enables exactly one. Because the clean service
  agrees with the model on every request we sent (below), any disagreement
  while a bug is enabled is attributable to that bug.
- **Strategies.** Baseline (9-request owner happy path); enumeration (each
  single request from fixture state, reset between cases, plus paging through
  lists); stateful uniform; stateful guided (the generator reads model state to
  pick plausible arguments). Scripted strategies stop at the first
  disagreement. "Requests to detection" counts HTTP requests sent up to and
  including the failing one.
- **Shrinking.** The first detection per bug is canonicalized (slots rewritten
  as resolved indices), then shrunk by chunk/single-op deletion, then by
  retargeting ops at fixture documents, then by deletion again. The predicate
  is "still disagrees with the model". The result is locally minimal (no
  single step can be removed), not guaranteed globally minimal.

## Clean service

| run | enumeration | baseline | stateful uniform | stateful guided |
| --- | --- | --- | --- | --- |
| seeds 0–9 | 3 × 1,962 requests, 0 mismatches | 9 requests, 0 | 25,000 requests, 0 | 25,000 requests, 0 |
| seeds 10–19 | 3 × 1,962 requests, 0 mismatches | 9 requests, 0 | 25,000 requests, 0 | 25,000 requests, 0 |

## Bugs caught

Totals are identical for both seed ranges. Stateful columns count a bug if any
of the 10 seeds caught it.

| strategy | caught | single-seed score (seeds 0–9) | single-seed score (seeds 10–19) |
| --- | ---: | --- | --- |
| baseline | 4/30 | n/a | n/a |
| enumeration | 24/30 | n/a | n/a |
| stateful uniform | 28/30 | 25–27 (median 26) | 24–27 (median 26) |
| stateful guided | 30/30 | 27–30 (median 28) | 26–28 (median 28) |

The baseline caught only the three schema bugs plus `patch-no-version-bump`
(its PATCH response shows `version` 1 instead of 2).

Enumeration caught 24 bugs, with a median of 28 requests to first detection
(range 1–656, depending on where in the case order the bug first shows).

### Bugs enumeration missed (20 seeds pooled)

| bug | why enumeration misses it | uniform | guided | guided median req |
| --- | --- | ---: | ---: | ---: |
| revoke-stale-index | needs revoke *then* list | 8/20 | 20/20 | 346.5 |
| editor-share-can-share | fixture has no editor-role user with an editor share | 3/20 | 18/20 | 977.5 |
| continuation-skips-acl | the only user with a page 2 in the fixture is an admin, who can see everything anyway | 14/20 | 15/20 | 756 |
| cursor-not-bound | needs one user's cursor replayed by another | 14/20 | 14/20 | 458.5 |
| editor-share-can-delete | same fixture gap as above | 0/20 | 5/20 | 1118 |
| reshare-keeps-old-role | needs share *then* a write by the grantee | 0/20 | 5/20 | 1116 |

`test_fixture_gap_one_extra_grant_lets_enumeration_catch_delete_bug` shows
that adding `"d003": {"bob": "editor"}` to the fixture makes enumeration catch
`editor-share-can-delete`, while the clean service still passes.

### Shrinking

- Seeds 0–9: shrunk stories of 1–3 steps (22 one-step, 6 two-step,
  2 three-step), median 1, from a median of 14 ops.
- Seeds 10–19: 21 one-step and 9 two-step, from a median of 13.5 ops.

## Runtime

- Seeds 0–9: 103.6 s wall-clock (about 78 s user CPU).
- Seeds 10–19: 383.5 s wall-clock (about 67 s user CPU). The process only got
  about 20% of a CPU because of other load on the machine.
- Unit tests: about 12 s.

## What these results do not show

- They don't show coverage of vulnerabilities I didn't seed. The 30 faults are
  hand-written, one per switch, and grouped into classes I chose.
- They don't independently validate the spec. The model and service share
  authors, so a shared misunderstanding is invisible.
- Enumeration's misses come from its definition (single requests from one
  fixture) and from the fixture's gaps. They are not a general claim that
  contract tests can't find these bugs.
- Stateful detection rates are tied to a 2,500-request budget, these seeds,
  and these generator weights.
- This is not a real deployment, real network, real auth, or real data.
