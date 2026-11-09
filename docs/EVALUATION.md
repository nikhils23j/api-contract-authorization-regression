# Evaluation — 9 November 2026

## Environment and commands

- macOS on arm64; Python 3.10.8; standard library only.
- From the repository root:

```sh
python3 -m unittest discover -s tests -v
python3 run_lab.py --json evaluation.json
```

The first command passed four tests. The second command exited successfully and produced the following result:

| Check | Result |
| --- | ---: |
| Clean service repetitions | 3 |
| Contract checks per clean repetition | 379 |
| Clean failures | 0 |
| Seeded mutation variants | 60 |
| Variants detected by full suite | 60/60 |
| Variants detected by owner-GET baseline | 5/60 |

By class, the full suite detected 40/40 scoped authorization bypasses, 10/10 missing-field schema defects, 8/8 tenant-list leaks, and 2/2 stale-write bypasses. The baseline detected five schema defects and none of the others.

## Workload and interpretation

Each clean run sends actual localhost HTTP requests across eight users, two tenants, five seeded documents, three item operations, list and create flows, invalid titles, and revision preconditions. The service resets in-memory state between state-changing scenarios. The runner compares responses with an oracle built from `contracts/documents.json`, rather than the service's authorization function.

The mutation set deliberately contains 40 separate principal/action/resource bypass cases, not 40 different vulnerability mechanisms. The baseline performs one owner GET and validates its schema; it is intentionally narrow. These results show that this suite rejected the specified local seeded variants. They do not establish coverage of unmodeled attacks, browser automation, deployment pipeline reliability, or a production security guarantee.
