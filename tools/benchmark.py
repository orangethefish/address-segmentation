"""Benchmark harness measuring latency, lifecycle phases, and memory in fresh subprocesses.

Measures fresh-process import, dictionary loading, matcher indexing, first cold request,
warmed requests, child total elapsed, full JSONL adapter end-to-end, and isolated memory.
Reports mean, p50, p95, p99, max, GC policy, and local threshold observations.
"""

import argparse
import gc
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import time
from typing import Any

from address_segmentation.io import load_dictionary
from address_segmentation.matcher import AddressMatcher
from tools.datasets import (
    _resolve_repo_root,
    get_platform_metadata,
    get_source_hash,
    load_cases,
    sha256_file,
)


def compute_percentile(sorted_values: list[int | float], p: float) -> float:
    """Compute percentile using linear interpolation between nearest ranks."""
    if not sorted_values:
        return 0.0
    n = len(sorted_values)
    if n == 1:
        return float(sorted_values[0])
    rank = (p / 100.0) * (n - 1)
    low = int(rank)
    high = min(low + 1, n - 1)
    weight = rank - low
    return float(sorted_values[low]) * (1.0 - weight) + float(sorted_values[high]) * weight


def compute_stats(values_ns: list[int | float]) -> dict[str, Any]:
    """Compute summary statistics for a list of nanosecond measurements."""
    if not values_ns:
        return {
            "count": 0,
            "mean_ns": 0.0,
            "mean_ms": 0.0,
            "min_ns": 0.0,
            "min_ms": 0.0,
            "p50_ns": 0.0,
            "p50_ms": 0.0,
            "p95_ns": 0.0,
            "p95_ms": 0.0,
            "p99_ns": 0.0,
            "p99_ms": 0.0,
            "max_ns": 0.0,
            "max_ms": 0.0,
        }
    s = sorted(values_ns)
    cnt = len(s)
    mean_ns = sum(s) / cnt
    min_ns = float(s[0])
    max_ns = float(s[-1])
    p50_ns = compute_percentile(s, 50.0)
    p95_ns = compute_percentile(s, 95.0)
    p99_ns = compute_percentile(s, 99.0)
    return {
        "count": cnt,
        "mean_ns": mean_ns,
        "mean_ms": mean_ns / 1_000_000.0,
        "min_ns": min_ns,
        "min_ms": min_ns / 1_000_000.0,
        "p50_ns": p50_ns,
        "p50_ms": p50_ns / 1_000_000.0,
        "p95_ns": p95_ns,
        "p95_ms": p95_ns / 1_000_000.0,
        "p99_ns": p99_ns,
        "p99_ms": p99_ns / 1_000_000.0,
        "max_ns": max_ns,
        "max_ms": max_ns / 1_000_000.0,
    }


def _run_worker(
    dict_path: str,
    cases_path: str,
    warmup: int,
    repetitions: int,
    seed: int,
    worker_out_path: str,
) -> int:
    """Private worker process: load, index, cold request, warm up, and measure latency."""
    rng = random.Random(seed)

    # Phase 1: Dictionary load
    t0 = time.perf_counter_ns()
    dict_data = load_dictionary(dict_path)
    load_ns = time.perf_counter_ns() - t0

    # Phase 2: Matcher index construction
    t0 = time.perf_counter_ns()
    matcher = AddressMatcher(
        streets=dict_data["streets"],
        wards=dict_data["wards"],
        provinces=dict_data["provinces"],
    )
    index_ns = time.perf_counter_ns() - t0

    # Phase 3: Load cases
    cases = load_cases(cases_path, dict_data)
    if not cases:
        raise ValueError("Cases dataset cannot be empty")

    num_cases = len(cases)

    # Phase 4: Cold first request BEFORE ANY WARMUPS
    first_case = cases[0]
    first_case_id = first_case.id
    first_input_len = len(first_case.address)

    t_cold_0 = time.perf_counter_ns()
    _ = matcher.identify(first_case.address)
    first_request_ns = time.perf_counter_ns() - t_cold_0

    # Phase 5: Warmup requests
    for w_idx in range(warmup):
        case_to_warm = cases[w_idx % num_cases]
        matcher.identify(case_to_warm.address)

    # Phase 6: Measured repetitions (all requests are warmed)
    warmed_timings_ns: list[int] = []
    request_records: list[dict[str, Any]] = []

    for rep in range(repetitions):
        order = list(range(num_cases))
        rng.shuffle(order)

        for case_idx in order:
            case = cases[case_idx]
            addr = case.address
            input_len = len(addr)

            t_req_0 = time.perf_counter_ns()
            _ = matcher.identify(addr)
            elapsed_ns = time.perf_counter_ns() - t_req_0

            warmed_timings_ns.append(elapsed_ns)
            request_records.append(
                {
                    "case_id": case.id,
                    "input_len": input_len,
                    "elapsed_ns": elapsed_ns,
                    "repetition": rep,
                }
            )

    worker_result = {
        "load_ns": load_ns,
        "index_ns": index_ns,
        "first_case_id": first_case_id,
        "first_request_ns": first_request_ns,
        "first_input_len": first_input_len,
        "warmup_count": warmup,
        "repetition_count": repetitions,
        "warmed_request_count": len(warmed_timings_ns),
        "warmed_timings_ns": warmed_timings_ns,
        "request_records": request_records,
    }

    with open(worker_out_path, "w", encoding="utf-8") as f:
        json.dump(worker_result, f)

    return 0


def _run_memory_worker(dict_path: str, cases_path: str, worker_out_path: str) -> int:
    """Private memory measurement process using tracemalloc and getrusage."""
    import tracemalloc

    tracemalloc.start()

    dict_data = load_dictionary(dict_path)
    matcher = AddressMatcher(
        streets=dict_data["streets"],
        wards=dict_data["wards"],
        provinces=dict_data["provinces"],
    )
    cases = load_cases(cases_path, dict_data)
    for case in cases:
        matcher.identify(case.address)

    current_bytes, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    ru_maxrss = None
    units = "unavailable"
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF)
        ru_maxrss = usage.ru_maxrss
        if sys.platform.startswith("linux"):
            units = "kibibytes"
        elif sys.platform == "darwin":
            units = "bytes"
        else:
            units = "platform_dependent"
    except (ImportError, AttributeError):
        pass

    mem_result = {
        "tracemalloc_peak_bytes": peak_bytes,
        "tracemalloc_current_bytes": current_bytes,
        "ru_maxrss": ru_maxrss,
        "ru_maxrss_units": units,
        "platform": sys.platform,
        "measurement_notes": (
            "tracemalloc measures Python heap allocations; "
            "ru_maxrss measures OS peak resident set size and includes tracemalloc overhead."
        ),
    }

    with open(worker_out_path, "w", encoding="utf-8") as f:
        json.dump(mem_result, f)

    return 0


def run_benchmark(argv: list[str] | None = None) -> int:
    """CLI driver for benchmark harness."""
    parser = argparse.ArgumentParser(
        description="Address segmentation benchmark: measure latency phases and memory."
    )
    parser.add_argument("--dictionary", required=True, help="Path to dictionary JSON.")
    parser.add_argument("--cases", required=True, help="Path to cases JSONL.")
    parser.add_argument("--output", required=True, help="Path to output JSON report.")
    parser.add_argument(
        "--processes",
        type=int,
        default=5,
        help="Number of fresh worker processes (default: 5).",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=10,
        help="Number of warmup requests per worker (default: 10).",
    )
    parser.add_argument(
        "--repetitions",
        type=int,
        default=3,
        help="Number of measured repetitions over dataset (default: 3).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for request ordering (default: 42).",
    )

    # Internal worker flags
    parser.add_argument("--_worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--_memory_worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--_worker_output", default=None, help=argparse.SUPPRESS)

    args = parser.parse_args(argv)

    if args._worker:
        return _run_worker(
            dict_path=args.dictionary,
            cases_path=args.cases,
            warmup=args.warmup,
            repetitions=args.repetitions,
            seed=args.seed,
            worker_out_path=args._worker_output,
        )

    if args._memory_worker:
        return _run_memory_worker(
            dict_path=args.dictionary,
            cases_path=args.cases,
            worker_out_path=args._worker_output,
        )

    # Validate arguments
    if args.processes <= 0:
        sys.stderr.write("Benchmark error: --processes must be a positive integer\n")
        return 1
    if args.warmup < 0:
        sys.stderr.write("Benchmark error: --warmup must be non-negative\n")
        return 1
    if args.repetitions <= 0:
        sys.stderr.write("Benchmark error: --repetitions must be a positive integer\n")
        return 1

    dict_path = Path(args.dictionary)
    if not dict_path.is_absolute():
        dict_path = Path.cwd() / dict_path

    cases_path = Path(args.cases)
    if not cases_path.is_absolute():
        cases_path = Path.cwd() / cases_path

    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = Path.cwd() / output_path

    try:
        dict_data = load_dictionary(str(dict_path))
        cases = load_cases(cases_path, dict_data)
        if not cases:
            sys.stderr.write("Benchmark error: Cases dataset cannot be empty\n")
            return 1
    except (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError, OSError) as exc:
        sys.stderr.write(f"Benchmark setup error: {exc}\n")
        return 1

    repo_root = _resolve_repo_root()
    env = os.environ.copy()
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{repo_root}{os.pathsep}{existing_pp}" if existing_pp else str(repo_root)
    )

    # 1. Fresh-process import measurement in dedicated subprocess (parent measures total elapsed, child measures internal import)
    try:
        import_cmd = [
            sys.executable,
            "-c",
            "import time; t0 = time.perf_counter_ns(); import address_segmentation; t1 = time.perf_counter_ns(); print(t1 - t0)",
        ]
        t_import_start = time.perf_counter_ns()
        proc = subprocess.run(
            import_cmd,
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        interpreter_and_import_elapsed_ns = time.perf_counter_ns() - t_import_start
        child_import_only_ns = int(proc.stdout.strip())
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        sys.stderr.write(f"Import measurement error: {exc}\n")
        return 1

    # 2. Full JSONL adapter end-to-end timing in dedicated subprocess
    try:
        adapter_input = "".join(
            json.dumps(c.address, ensure_ascii=False) + "\n" for c in cases
        )
        t_adapt_0 = time.perf_counter_ns()
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "address_segmentation.io",
                "--dictionary",
                str(dict_path),
            ],
            input=adapter_input,
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        full_jsonl_adapter_ns = time.perf_counter_ns() - t_adapt_0
    except (OSError, subprocess.SubprocessError) as exc:
        sys.stderr.write(f"Adapter measurement error: {exc}\n")
        return 1

    # 3. Dedicated isolated memory measurement subprocess
    mem_result: dict[str, Any] = {}
    with tempfile.NamedTemporaryFile(
        suffix=".json", prefix="bench_mem_", delete=False
    ) as tmp_mem:
        tmp_mem_path = Path(tmp_mem.name)

    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.benchmark",
                "--_memory_worker",
                "--dictionary",
                str(dict_path),
                "--cases",
                str(cases_path),
                "--output",
                str(output_path),
                "--_worker_output",
                str(tmp_mem_path),
            ],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        with open(tmp_mem_path, "r", encoding="utf-8") as f:
            mem_result = json.load(f)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"Memory worker error: {exc}\n")
        return 1
    finally:
        if tmp_mem_path.exists():
            tmp_mem_path.unlink()

    # 4. Fresh worker processes measuring latency phases
    worker_loads: list[int] = []
    worker_indexes: list[int] = []
    first_requests: list[int] = []
    first_case_ids: list[str] = []
    all_warmed_timings: list[int] = []
    child_elapsed_timings: list[int] = []
    all_raw_request_records: list[dict[str, Any]] = []

    for p_idx in range(args.processes):
        child_seed = args.seed + p_idx * 1000
        with tempfile.NamedTemporaryFile(
            suffix=".json", prefix=f"bench_worker_{p_idx}_", delete=False
        ) as tmp_w:
            tmp_worker_path = Path(tmp_w.name)

        try:
            t_child_0 = time.perf_counter_ns()
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tools.benchmark",
                    "--_worker",
                    "--dictionary",
                    str(dict_path),
                    "--cases",
                    str(cases_path),
                    "--output",
                    str(output_path),
                    "--warmup",
                    str(args.warmup),
                    "--repetitions",
                    str(args.repetitions),
                    "--seed",
                    str(child_seed),
                    "--_worker_output",
                    str(tmp_worker_path),
                ],
                capture_output=True,
                text=True,
                env=env,
                check=True,
            )
            child_elapsed_ns = time.perf_counter_ns() - t_child_0
            child_elapsed_timings.append(child_elapsed_ns)

            with open(tmp_worker_path, "r", encoding="utf-8") as f:
                w_data = json.load(f)

            worker_loads.append(w_data["load_ns"])
            worker_indexes.append(w_data["index_ns"])
            if w_data["first_request_ns"] is not None:
                first_requests.append(w_data["first_request_ns"])
            if "first_case_id" in w_data:
                first_case_ids.append(w_data["first_case_id"])
            all_warmed_timings.extend(w_data["warmed_timings_ns"])
            for rec in w_data["request_records"]:
                rec["process_index"] = p_idx
                all_raw_request_records.append(rec)
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
            sys.stderr.write(f"Worker {p_idx} error: {exc}\n")
            return 1
        finally:
            if tmp_worker_path.exists():
                tmp_worker_path.unlink()

    warmed_stats = compute_stats(all_warmed_timings)
    first_request_stats = compute_stats(first_requests)
    load_stats = compute_stats(worker_loads)
    index_stats = compute_stats(worker_indexes)
    child_elapsed_stats = compute_stats(child_elapsed_timings)
    import_stats = compute_stats([child_import_only_ns])
    interpreter_and_import_stats = compute_stats([interpreter_and_import_elapsed_ns])
    adapter_stats = compute_stats([full_jsonl_adapter_ns])

    input_lengths = [len(c.address) for c in cases]
    input_len_summary = {
        "min": min(input_lengths) if input_lengths else 0,
        "max": max(input_lengths) if input_lengths else 0,
        "mean": sum(input_lengths) / len(input_lengths) if input_lengths else 0.0,
    }

    report = {
        "metadata": {
            "dictionary_path": str(dict_path),
            "dictionary_hash": sha256_file(dict_path),
            "cases_path": str(cases_path),
            "cases_hash": sha256_file(cases_path),
            "source_hash": get_source_hash(),
            "platform": get_platform_metadata(),
            "processes": args.processes,
            "warmup": args.warmup,
            "repetitions": args.repetitions,
            "seed": args.seed,
            "total_cases": len(cases),
            "input_lengths": input_len_summary,
        },
        "gc_policy": {
            "is_enabled": gc.isenabled(),
            "thresholds": list(gc.get_threshold()),
            "counts": list(gc.get_count()),
        },
        "phases": {
            "fresh_process_import": import_stats,
            "interpreter_and_import_total": interpreter_and_import_stats,
            "dictionary_load": load_stats,
            "matcher_index": index_stats,
            "first_request": first_request_stats,
            "warmed_request": warmed_stats,
            "child_total_elapsed": child_elapsed_stats,
            "full_jsonl_adapter": adapter_stats,
        },
        "memory": mem_result,
        "thresholds": {
            "mean_request_ms_target": 10.0,
            "max_request_ms_target": 100.0,
            "observed_warmed_mean_ms": warmed_stats["mean_ms"],
            "observed_warmed_max_ms": warmed_stats["max_ms"],
            "observed_first_request_max_ms": first_request_stats["max_ms"],
            "observed_overall_max_ms": max(
                warmed_stats["max_ms"], first_request_stats["max_ms"]
            ),
            "mean_target_met": (warmed_stats["mean_ms"] <= 10.0),
            "warm_max_target_met": (warmed_stats["max_ms"] <= 100.0),
            "all_requests_within_target": (
                warmed_stats["max_ms"] <= 100.0
                and first_request_stats["max_ms"] <= 100.0
            ),
            "cold_request_case_ids": first_case_ids,
            "observation_note": (
                "Local observation only; not a universal guarantee under unknown official grader environment."
            ),
        },
        "raw_request_timings": all_raw_request_records,
    }

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
            f.write("\n")
    except OSError as exc:
        sys.stderr.write(f"Benchmark output write error: {exc}\n")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(run_benchmark())
