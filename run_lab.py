"""Full evaluation: clean runs + every seeded bug x every test strategy.

    python3 run_lab.py                      # full run, writes results/results.json + results/summary.md
    python3 run_lab.py --quick              # CI-sized run
    python3 run_lab.py --only revoke-stale-index --story   # one bug, print the shrunk repro
"""
import argparse
from concurrent.futures import ProcessPoolExecutor
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from aclreg.contract import load_contract  # noqa: E402
from aclreg.harness import Harness  # noqa: E402
from aclreg.mutations import BY_NAME, CLASSES, all_mutations  # noqa: E402
from aclreg.strategies import (  # noqa: E402
    baseline_cases, enumeration_cases, run_cases, run_stateful)

STRATEGIES = ["baseline", "enumeration", "stateful_uniform", "stateful_guided"]


def environment():
    def sysctl(key):
        try:
            return subprocess.run(["sysctl", "-n", key], capture_output=True, text=True,
                                  timeout=2).stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            return None
    try:
        load = os.getloadavg()
    except OSError:
        load = None
    return {
        "date_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu": sysctl("machdep.cpu.brand_string") or platform.processor(),
        "load_average_at_start": load,
        "stdlib_only": True,
    }


def seed_summary(runs):
    hits = [r["requests_to_detection"] for r in runs if r["detected"]]
    return {
        "seeds_detected": len(hits),
        "seeds": len(runs),
        "requests_to_detection": [r["requests_to_detection"] for r in runs],
        "median_requests": statistics.median(hits) if hits else None,
        "min_requests": min(hits) if hits else None,
        "max_requests": max(hits) if hits else None,
    }


def evaluate_mutant(contract, mutation, seeds, budget, length, cases):
    with Harness(contract, [mutation.name]) as harness:
        base = run_cases(harness, baseline_cases())
        enum = run_cases(harness, cases)
        uniform = [run_stateful(harness, s, budget, length, guided=False) for s in seeds]
        guided = [run_stateful(harness, s, budget, length, guided=True) for s in seeds]
        shrink = None
        # shrink the first detecting run (guided first, then uniform, then enumeration)
        source = next((r for r in guided + uniform if r["detected"]), None)
        origin = "stateful"
        if source is None and enum["detected"]:
            source, origin = enum, "enumeration"
        if source is not None:
            minimal, replays = harness.shrink(source["failing_case"])
            shrink = {
                "from": origin,
                "original_ops": len(source["failing_case"]),
                "shrunk_ops": len(minimal),
                "replays": replays,
                "ops": [list(op) for op in minimal],
                "story": harness.story(minimal),
            }
    return {
        "name": mutation.name,
        "class": mutation.bug_class,
        "description": mutation.description,
        "baseline": {"detected": base["detected"], "requests_to_detection": base["requests_to_detection"]},
        "enumeration": {"detected": enum["detected"], "requests_to_detection": enum["requests_to_detection"],
                        "first_problem": enum["problems"][0] if enum["problems"] else None},
        "stateful_uniform": seed_summary(uniform),
        "stateful_guided": seed_summary(guided),
        "shrink": shrink,
    }


def _evaluate_job(job):
    return evaluate_mutant(*job)


def _print_entry(mutation, entry, story):
    u, g = entry["stateful_uniform"], entry["stateful_guided"]
    shrink = entry["shrink"]
    shrunk = f"{shrink['original_ops']}->{shrink['shrunk_ops']}" if shrink else "-"
    print(f"{mutation.name:30s} base={'Y' if entry['baseline']['detected'] else '.'} "
          f"enum={'Y' if entry['enumeration']['detected'] else '.'} "
          f"uniform={u['seeds_detected']:>2}/{u['seeds']} guided={g['seeds_detected']:>2}/{g['seeds']} "
          f"shrunk={shrunk}", flush=True)
    if story and shrink:
        print("\n".join("    " + line for line in shrink["story"]))



def detected(entry, strategy):
    data = entry[strategy]
    return data["detected"] if "detected" in data else data["seeds_detected"] > 0


def class_table(mutants):
    table = {}
    for bug_class in CLASSES:
        rows = [m for m in mutants if m["class"] == bug_class]
        table[bug_class] = {"total": len(rows)}
        for strategy in STRATEGIES:
            table[bug_class][strategy] = sum(detected(m, strategy) for m in rows)
    return table


def per_seed_scores(mutants, strategy, seeds):
    """Mutation score of each individual seed (fraction of bugs that seed found)."""
    scores = []
    for index in range(len(seeds)):
        found = sum(m[strategy]["requests_to_detection"][index] is not None for m in mutants)
        scores.append(found)
    return scores


def render_summary(report):
    env, cfg, clean = report["environment"], report["config"], report["clean"]
    total = len(report["mutants"])
    out = [f"# Evaluation results ({env['date_utc']})", "",
           f"Generated by `{cfg['command']}`; do not edit by hand.", "",
           f"- Python {env['python']} on {env['platform']} ({env['cpu']}); standard library only.",
           f"- Seeds {cfg['seeds'][0]}..{cfg['seeds'][-1]} ({len(cfg['seeds'])} seeds); "
           f"stateful budget {cfg['budget']} requests per seed, sequences of {cfg['length']} ops.",
           f"- Wall time {report['runtime_seconds']} s (load average at start: {env['load_average_at_start']}).",
           "", "## Clean service (no bugs enabled)", "",
           f"- Enumeration: {len(clean['enumeration'])} runs x {clean['enumeration'][0]['requests']} requests, "
           f"mismatches {[r['mismatches'] for r in clean['enumeration']]}",
           f"- Baseline: {clean['baseline']['requests']} requests, mismatches {clean['baseline']['mismatches']}",
           f"- Stateful uniform: {sum(r['requests'] for r in clean['stateful_uniform'])} requests over "
           f"{len(clean['stateful_uniform'])} seeds, mismatches {sum(r['mismatches'] for r in clean['stateful_uniform'])}",
           f"- Stateful guided: {sum(r['requests'] for r in clean['stateful_guided'])} requests over "
           f"{len(clean['stateful_guided'])} seeds, mismatches {sum(r['mismatches'] for r in clean['stateful_guided'])}",
           "", "## Seeded bugs caught, by class", "",
           "Stateful columns count a bug as caught if at least one seed caught it.", "",
           "| class | bugs | baseline | enumeration | stateful uniform | stateful guided |",
           "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for name, row in report["by_class"].items():
        out.append(f"| {name} | {row['total']} | " + " | ".join(str(row[s]) for s in STRATEGIES) + " |")
    s = report["summary"]
    out.append(f"| **all** | **{total}** | " + " | ".join(f"**{s[k]['caught']}**" for k in STRATEGIES) + " |")
    out += ["", "Per-seed mutation score (bugs caught by a single seed, out of "
            f"{total}): uniform {s['stateful_uniform']['per_seed']}, guided {s['stateful_guided']['per_seed']}.", "",
            "## Every bug", "",
            "| bug | class | baseline | enumeration (req) | uniform seeds / median req | guided seeds / median req | shrunk ops |",
            "| --- | --- | --- | --- | --- | --- | --- |"]
    for m in report["mutants"]:
        e = m["enumeration"]
        u, g = m["stateful_uniform"], m["stateful_guided"]
        shrink = m["shrink"]
        shrunk = f"{shrink['original_ops']} -> {shrink['shrunk_ops']}" if shrink else "-"
        out.append(
            f"| {m['name']} | {m['class']} | {'yes' if m['baseline']['detected'] else 'no'} | "
            f"{e['requests_to_detection'] if e['detected'] else 'missed'} | "
            f"{u['seeds_detected']}/{u['seeds']} / {u['median_requests']} | "
            f"{g['seeds_detected']}/{g['seeds']} / {g['median_requests']} | {shrunk} |")
    out += ["", "## Shrunk repro stories", ""]
    for m in report["mutants"]:
        if m["shrink"]:
            out += [f"### {m['name']}", "", "```", *m["shrink"]["story"], "```", ""]
    return "\n".join(out) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seeds", type=int, default=10, help="number of seeds")
    parser.add_argument("--seed-start", type=int, default=0, help="first seed")
    parser.add_argument("--budget", type=int, default=2500, help="stateful requests per seed")
    parser.add_argument("--length", type=int, default=25, help="ops per random sequence")
    parser.add_argument("--quick", action="store_true", help="3 seeds x 600 requests (CI)")
    parser.add_argument("--only", help="evaluate a single bug by name")
    parser.add_argument("--story", action="store_true", help="print shrunk repro stories")
    parser.add_argument("--workers", type=int, default=4,
                        help="parallel processes, one bug each (results do not depend on this)")
    parser.add_argument("--out", type=Path, default=None,
                        help="results directory (default: results/ for full runs, nothing for --quick/--only)")
    args = parser.parse_args()
    if args.quick:
        args.seeds, args.budget = 3, 600
    seeds = list(range(args.seed_start, args.seed_start + args.seeds))
    contract = load_contract()
    started = time.time()
    env = environment()
    cases = enumeration_cases()

    clean = {"enumeration": [], "baseline": None, "stateful_uniform": [], "stateful_guided": []}
    with Harness(contract) as harness:
        for _ in range(3):
            r = run_cases(harness, cases)
            clean["enumeration"].append({"requests": r["requests"], "mismatches": int(r["detected"]),
                                         "problem": r["problems"]})
        r = run_cases(harness, baseline_cases())
        clean["baseline"] = {"requests": r["requests"], "mismatches": int(r["detected"])}
        for guided, key in ((False, "stateful_uniform"), (True, "stateful_guided")):
            for seed in seeds:
                r = run_stateful(harness, seed, args.budget, args.length, guided=guided)
                clean[key].append({"seed": seed, "requests": r["requests"], "mismatches": int(r["detected"]),
                                   "problem": r["problems"], "failing_case": r["failing_case"]})
    clean_mismatches = (sum(r["mismatches"] for r in clean["enumeration"]) + clean["baseline"]["mismatches"]
                        + sum(r["mismatches"] for r in clean["stateful_uniform"] + clean["stateful_guided"]))
    print(f"clean service: {clean_mismatches} mismatches "
          f"(enumeration 3 x {clean['enumeration'][0]['requests']} requests, "
          f"stateful 2 modes x {len(seeds)} seeds x {args.budget} requests)")

    targets = [BY_NAME[args.only]] if args.only else all_mutations()
    mutants = []
    jobs = [(contract, m, seeds, args.budget, args.length, cases) for m in targets]
    with ProcessPoolExecutor(max_workers=max(1, args.workers)) as pool:
        results = pool.map(_evaluate_job, jobs)
        for mutation, entry in zip(targets, results):
            mutants.append(entry)
            _print_entry(mutation, entry, args.story)

    summary = {}
    for strategy in STRATEGIES:
        summary[strategy] = {"caught": sum(detected(m, strategy) for m in mutants), "total": len(mutants)}
    for strategy in ("stateful_uniform", "stateful_guided"):
        per_seed = per_seed_scores(mutants, strategy, seeds)
        summary[strategy].update(per_seed=per_seed, per_seed_min=min(per_seed), per_seed_max=max(per_seed),
                                 per_seed_median=statistics.median(per_seed))
    summary["caught_by_any"] = sum(any(detected(m, s) for s in STRATEGIES) for m in mutants)
    summary["missed_by_enumeration_caught_by_guided"] = [
        m["name"] for m in mutants if not m["enumeration"]["detected"] and detected(m, "stateful_guided")]
    shrinks = [m["shrink"] for m in mutants if m["shrink"]]
    if shrinks:
        summary["shrink"] = {
            "median_original_ops": statistics.median(s["original_ops"] for s in shrinks),
            "median_shrunk_ops": statistics.median(s["shrunk_ops"] for s in shrinks),
            "max_shrunk_ops": max(s["shrunk_ops"] for s in shrinks),
        }

    report = {
        "environment": env,
        "config": {"command": "python3 " + " ".join([Path(sys.argv[0]).name] + sys.argv[1:]),
                   "seeds": seeds, "budget": args.budget, "length": args.length, "workers": args.workers,
                   "enumeration_cases": len(cases)},
        "runtime_seconds": round(time.time() - started, 1),
        "clean": clean,
        "clean_mismatches": clean_mismatches,
        "summary": summary,
        "by_class": class_table(mutants),
        "mutants": mutants,
    }
    print(json.dumps({"clean_mismatches": clean_mismatches, "runtime_seconds": report["runtime_seconds"],
                      "summary": summary, "by_class": report["by_class"]}, indent=1))
    out = args.out if args.out else (None if (args.quick or args.only) else ROOT / "results")
    if out:
        out.mkdir(parents=True, exist_ok=True)
        (out / "results.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
        (out / "summary.md").write_text(render_summary(report), encoding="utf-8")
        print(f"wrote {out / 'results.json'} and {out / 'summary.md'}")
    if clean_mismatches:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
