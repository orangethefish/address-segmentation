"""Measure the complete exported Solution.process call under the teacher's timer."""

import argparse
from math import ceil
from pathlib import Path
import random
import runpy
from statistics import fmean
from time import perf_counter_ns
import tracemalloc

from benchmarks.generate import generate_synthetic_cases, generate_synthetic_dictionaries
from tools.datasets import EvaluationCase, canonical_json_hash, get_platform_metadata, sha256_file
from tools.evaluate_teacher_v2 import load_teacher_v2, score_teacher_v2, validate_result, write_report

MEAN_LIMIT_NS = 10_000_000
MAX_LIMIT_NS = 100_000_000


def stress_workload(name):
    if name == "scale":
        rng = random.Random(47)
        plural = generate_synthetic_dictionaries(23_800, 340, 34, rng)
        inventories = {field.removesuffix("s"): labels for field, labels in plural.items()}
        cases = [EvaluationCase(**row) for row in generate_synthetic_cases(200, plural, rng)]
    else:
        inventories = {
            "street": ["Lê Duẩn", "3/2", "Đặng Tất"],
            "ward": ["Phường Minh An", "Xã Tân Lợi"],
            "province": ["Thành phố Huế", "Tỉnh Lào Cai"],
        }
        expected = {"street": "3/2", "ward": "Phường Minh An", "province": "Thành phố Huế"}
        cases = []
        for repeats in (16, 128, 512, 1024):
            for noise in ("gần cửa kho cũ ", "tầng che khu tạm; ", "Nhiễu" * 8, "Pclutter, "):
                cid = str(len(cases) + 1)
                # A whitespace boundary keeps the noise prefix separate, including
                # the long uninterrupted-token scenario.
                address = f"{(noise * repeats).rstrip()} 74/49/3 Đường3/2, PhườngMinh An, Thành phốHuế"
                cases.append(EvaluationCase(cid, address, expected, ("long-noisy",), cid, "recoverable"))

    return inventories, cases, {
        "dataset": f"synthetic-{name}", "official_data": False, "case_count": len(cases),
        "dictionary_counts": {field: len(labels) for field, labels in inventories.items()},
        "source_hashes": {"inventory": canonical_json_hash(inventories),
                          "cases": canonical_json_hash([case.to_dict() for case in cases])},
    }


def timing_summary(samples):
    ordered = sorted(samples)
    return {
        "mean_ns": fmean(ordered),
        **{f"p{p}_ns": ordered[ceil(len(ordered) * p / 100) - 1] for p in (50, 95, 99)},
        "maximum_ns": ordered[-1],
    }


def run_pass(solution_type, inventories, cases):
    start = perf_counter_ns()
    solution = solution_type(inventories["province"], inventories["ward"], inventories["street"])
    construction = perf_counter_ns() - start

    predictions, exceptions, durations = {}, {}, {}
    for case in cases:
        start = perf_counter_ns()
        try:
            result = solution.process(case.address)
        except Exception as error:
            finish = perf_counter_ns()
            exceptions[case.id] = f"{type(error).__name__}: {error}"
        else:
            finish = perf_counter_ns()
            try:
                validate_result(result)
            except ValueError as error:
                exceptions[case.id] = str(error)
            else:
                predictions[case.id] = result
        durations[case.id] = finish - start

    by_id = {case.id: case for case in cases}
    slowest = sorted(durations, key=durations.get, reverse=True)[:5]
    return {
        **timing_summary(durations.values()),
        "construction_ns": construction,
        "first_call_ns": durations[cases[0].id],
        "per_case_ns": durations,
        "slowest": [{"id": cid, "ns": durations[cid], "address": by_id[cid].address}
                    for cid in slowest],
        "metrics": score_teacher_v2(cases, predictions, exceptions),
        "exceptions": exceptions,
    }, predictions


def benchmark(script, inventories, cases, metadata, passes=5):
    start = perf_counter_ns()
    solution_type = runpy.run_path(str(script))["Solution"]
    load_ns = perf_counter_ns() - start
    runs, predictions = [], None
    stable = True
    for _ in range(passes):
        run, observed = run_pass(solution_type, inventories, cases)
        if predictions is not None and observed != predictions:
            stable = False
        predictions = observed
        runs.append(run)

    # Measure Python allocations in a separate construction. Allocation
    # tracing stays out of every timed pass.
    tracemalloc.start()
    try:
        solution = solution_type(inventories["province"], inventories["ward"], inventories["street"])
        retained, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    del solution

    overall = timing_summary([ns for run in runs for ns in run["per_case_ns"].values()])
    within_limits = all(run["mean_ns"] <= MEAN_LIMIT_NS and run["maximum_ns"] <= MAX_LIMIT_NS
                        for run in runs)
    return {
        "metadata": {**metadata, "platform": get_platform_metadata(), "solution": str(script),
                     "solution_sha256": sha256_file(script), "passes": passes,
                     "timer": "perf_counter_ns around Solution.process only, including the first call",
                     "percentile_method": "nearest rank"},
        "import_ns": load_ns,
        "construction_memory_bytes": {"retained": retained, "peak": peak},
        "overall": overall,
        "limits_ns": {"mean": MEAN_LIMIT_NS, "maximum": MAX_LIMIT_NS},
        "within_limits": within_limits,
        "stable_predictions": stable,
        "runs": runs,
        "predictions": predictions,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--solution", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path, default=Path("teacher-info/v2_2_level_address"))
    parser.add_argument("--workload", choices=("public", "scale", "noisy"), default="public")
    parser.add_argument("--passes", type=int, default=5)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.passes < 1:
        parser.error("--passes must be positive")

    if args.workload == "public":
        inventories, cases, metadata = load_teacher_v2(args.source_dir)
    else:
        inventories, cases, metadata = stress_workload(args.workload)
    report = benchmark(args.solution, inventories, cases, metadata, args.passes)
    write_report(args.output, report)
    budget = report["overall"]
    print(f'{args.workload}: mean {budget["mean_ns"] / 1e6:.3f} ms, max {budget["maximum_ns"] / 1e6:.3f} ms')
    return (not report["within_limits"] or not report["stable_predictions"]
            or any(run["exceptions"] for run in report["runs"]))


if __name__ == "__main__":
    raise SystemExit(main())
