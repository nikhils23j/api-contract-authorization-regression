import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from aclreg.contract import load_contract
from aclreg.mutations import all_mutations
from aclreg.runner import run_suite


def main():
    parser = argparse.ArgumentParser(description="Run contract and authorization regression lab")
    parser.add_argument("--json", type=Path, help="write machine-readable evaluation report")
    args = parser.parse_args()
    contract = load_contract()
    clean = [run_suite(contract) for _ in range(3)]
    mutants = all_mutations(contract)
    evaluated = []
    for mutation in mutants:
        full = run_suite(contract, mutation)
        baseline = run_suite(contract, mutation, naive=True)
        evaluated.append({
            "name": mutation.name,
            "kind": mutation.kind,
            "detected_full": bool(full["failures"]),
            "detected_baseline": bool(baseline["failures"]),
            "full_checks": full["checks"],
            "first_failure": full["failures"][0] if full["failures"] else None,
        })
    report = {
        "clean_runs": len(clean),
        "clean_checks_per_run": [item["checks"] for item in clean],
        "clean_failures": sum(len(item["failures"]) for item in clean),
        "mutants": len(mutants),
        "detected_full": sum(item["detected_full"] for item in evaluated),
        "detected_baseline": sum(item["detected_baseline"] for item in evaluated),
        "by_kind": {
            kind: {
                "total": sum(item["kind"] == kind for item in evaluated),
                "detected_full": sum(item["kind"] == kind and item["detected_full"] for item in evaluated),
                "detected_baseline": sum(item["kind"] == kind and item["detected_baseline"] for item in evaluated),
            }
            for kind in sorted({item["kind"] for item in evaluated})
        },
        "mutations": evaluated,
    }
    print(json.dumps({key: value for key, value in report.items() if key != "mutations"}, indent=2))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if report["clean_failures"] or report["detected_full"] != report["mutants"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
