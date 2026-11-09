# API Contract and Authorization Regression Lab

An AI-assisted portfolio project for practicing contract-driven API testing. It runs entirely on a personal laptop with Python 3.10+ and the standard library. The sample service holds generated tenant and document data in memory; it contains no employer or customer information.

## What is tested

The repository contains a real localhost HTTP service and a separate test runner. The declarative [contract](contracts/documents.json) names eight users in two tenants, five documents, and the required response fields. The runner derives cases for GET, PATCH, and DELETE across every user/document pair, including anonymous access. It also checks tenant-filtered lists, creation, schema types and fields, invalid input, and optimistic concurrency through `If-Match`.

The service deliberately keeps its authorization implementation separate from the contract oracle. The mutation catalogue introduces **60 seeded variants**: 40 narrowly scoped authorization bypasses, 10 missing response fields, eight cross-tenant list leaks, and two stale-write bypasses. The evaluation compares the full suite with a deliberately limited owner-GET happy-path baseline. A detected variant means the test suite rejected that particular seeded defect; it does not prove security against unmodeled defects.

## Run

```sh
python3 -m unittest discover -s tests -v
python3 run_lab.py --json evaluation.json
```

`run_lab.py` exits nonzero if the clean service fails or any seeded variant escapes the full suite. The JSON report records every variant and its first failing assertion. The program binds only to `127.0.0.1` on an ephemeral port and shuts down each server after its run. No packages, cloud account, browser, database, or paid service are required.

## Design choices

- **HTTP rather than mocked calls:** tests exercise parsing, status codes, headers, and JSON serialization over the local socket.
- **Contract oracle:** policy expectations come from the declarative fixture, while the service implements checks separately. This limits, but cannot eliminate, correlated mistakes.
- **Deterministic mutation set:** each variant is named and repeatable, enabling a reproducible comparison against the simple baseline.
- **Isolation:** the service resets fixture state before state-changing cases, so one request cannot contaminate the next.
- **Fail-fast evaluation:** a variant is detected at the first contract violation; the clean service runs all checks three times.

## Limits

This is a local, in-memory lab and is not a production API security assessment. The 40 authorization variants are separate principal/action/resource cases, not 40 distinct vulnerability classes. The baseline checks only one owner GET; its low detection rate is an intentionally narrow comparison. There is no Selenium, Cypress, TestNG, Jenkins, SAST, SCA, DAST, or real cloud integration. The project does not create professional QA employment experience.
