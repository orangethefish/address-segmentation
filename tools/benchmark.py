"""Benchmark harness measuring latency, lifecycle phases, and memory in fresh subprocesses.

Primary latency is parent wall time to complete stdout for one address in a new
CLI process, including startup, imports, dictionary loading, indexing and I/O.
Warm identify calls and a shared-process batch are separate diagnostics.
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
from threading import Thread
from typing import Any

from address_segmentation.io import load_dictionary, load_config
from dataclasses import asdict
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


def measure_cli(
    command: list[str], addresses: list[str], env: dict[str, str], timeout: float
) -> tuple[int, int, list[dict[str, str | None]]]:
    """Time complete stdout arrival and process exit; validate after timing.

    Encoding test input and scoring output belong to the harness, outside timing.
    Pipe transfer, JSON decoding/encoding in the CLI and flushing are timed;
    process exit is recorded separately.
    A reader drains stdout concurrently so a large batch cannot block on a full
    pipe. Pipe/thread setup precedes timing; the child runs its normal CLI.
    """
    if not addresses:
        raise ValueError("At least one address is required")
    payload = "".join(json.dumps(address, ensure_ascii=False) + "\n"
                      for address in addresses).encode("utf-8")
    stdout_read, stdout_write = os.pipe()
    received: list[tuple[int, bytes]] = []

    def read_stdout() -> None:
        with os.fdopen(stdout_read, "rb") as stream:
            for line in iter(stream.readline, b""):
                received.append((time.perf_counter_ns(), line))

    reader = Thread(target=read_stdout, daemon=True)
    reader.start()
    try:
        start = time.perf_counter_ns()
        try:
            proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=stdout_write,
                                    stderr=subprocess.PIPE, env=env)
        finally:
            os.close(stdout_write)
        with proc:
            try:
                _, stderr = proc.communicate(input=payload, timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                raise
            exit_ns = time.perf_counter_ns() - start
            if proc.returncode:
                raise subprocess.CalledProcessError(proc.returncode, command, stderr=stderr)
    finally:
        reader.join()
    lines = [line.decode("utf-8").rstrip("\r\n") for _, line in received]
    if len(lines) != len(addresses):
        raise ValueError(f"CLI returned {len(lines)} lines for {len(addresses)} addresses")
    predictions = []
    for line in lines:
        result = json.loads(line)
        if (not isinstance(result, dict)
                or set(result) != {"street", "ward", "province"}
                or any(value is not None and not isinstance(value, str)
                       for value in result.values())):
            raise ValueError("CLI output must contain exactly three string-or-null fields")
        predictions.append(result)
    output_ns = received[-1][0] - start
    return output_ns, exit_ns, predictions


def _run_worker(
    dict_path: str,
    cases_path: str,
    warmup: int,
    repetitions: int,
    seed: int,
    worker_out_path: str,
    config_path: str | None = None,
) -> int:
    """Diagnostic worker: first identify is measured AFTER loading and indexing."""
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
        **({"config": load_config(config_path)} if config_path else {}),
    )
    index_ns = time.perf_counter_ns() - t0

    # Phase 3: Load cases
    cases = load_cases(cases_path, dict_data)
    if not cases:
        raise ValueError("Cases dataset cannot be empty")

    num_cases = len(cases)

    # Phase 4: First identify BEFORE warmups, but AFTER index construction.
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


def _run_memory_worker(dict_path: str, cases_path: str, worker_out_path: str, config_path: str | None = None) -> int:
    """Private memory measurement process using tracemalloc and getrusage."""
    import tracemalloc

    tracemalloc.start()

    dict_data = load_dictionary(dict_path)
    matcher = AddressMatcher(
        streets=dict_data["streets"],
        wards=dict_data["wards"],
        provinces=dict_data["provinces"],
        **({"config": load_config(config_path)} if config_path else {}),
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
        help="Independent rounds of cold CLI, batch CLI and warm diagnostics (default: 5).",
    )
    parser.add_argument(
        "--cold-cases", type=int, default=0,
        help="Cold cases per round: 0 measures every case (default); N selects a seeded uniform sample. "
             "Each address starts its own process and rebuilds the full dictionary.",
    )
    parser.add_argument(
        "--cli-timeout", type=float, default=30.0,
        help="Seconds allowed per cold or batch CLI process; timeout fails the benchmark (default: 30).",
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

    parser.add_argument("--config", help="Optional MatcherConfig JSON file")
    args = parser.parse_args(argv)
    config_path = str(Path(args.config).resolve()) if args.config else None
    config_args = ["--config", config_path] if config_path else []

    if args._worker:
        return _run_worker(
            dict_path=args.dictionary,
            cases_path=args.cases,
            warmup=args.warmup,
            repetitions=args.repetitions,
            seed=args.seed,
            worker_out_path=args._worker_output,
            config_path=config_path,
        )

    if args._memory_worker:
        return _run_memory_worker(
            dict_path=args.dictionary,
            cases_path=args.cases,
            worker_out_path=args._worker_output,
            config_path=config_path,
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
    if args.cold_cases < 0:
        sys.stderr.write("Benchmark error: --cold-cases must be non-negative\n")
        return 1
    if not 0 < args.cli_timeout < float("inf"):
        sys.stderr.write("Benchmark error: --cli-timeout must be finite and positive\n")
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

    # 2. Primary grading assumption: each address gets a fresh interpreter/index.
    # No benchmark/evaluation code or report file is involved in the measured CLI.
    adapter_command = [sys.executable, "-m", "address_segmentation.io",
                       "--dictionary", str(dict_path), *config_args]
    selected_cases = list(cases)
    if args.cold_cases and args.cold_cases < len(cases):
        selected_cases = random.Random(args.seed).sample(cases, args.cold_cases)
    cold_records: list[dict[str, Any]] = []
    batch_timings: list[int] = []
    batch_exit_timings: list[int] = []
    batch_matches: list[int] = []
    try:
        for round_index in range(args.processes):
            order = list(selected_cases)
            random.Random(args.seed + round_index * 1000).shuffle(order)
            for case in order:
                elapsed_ns, exit_ns, predictions = measure_cli(
                    adapter_command, [case.address], env, args.cli_timeout)
                cold_records.append({
                    "case_id": case.id, "input_len": len(case.address),
                    "round": round_index, "elapsed_ns": elapsed_ns, "process_exit_ns": exit_ns,
                    "prediction": predictions[0],
                    "matches_expected": predictions[0] == case.expected,
                })
            # A throughput comparison, never a substitute for cold request latency.
            batch_ns, batch_exit_ns, predictions = measure_cli(
                adapter_command, [case.address for case in cases], env, args.cli_timeout)
            batch_timings.append(batch_ns)
            batch_exit_timings.append(batch_exit_ns)
            batch_matches.append(sum(pred == case.expected
                                     for pred, case in zip(predictions, cases)))
            sys.stderr.write(f"CLI round {round_index + 1}/{args.processes}: "
                             f"{len(order)} fresh single-address processes + one batch complete\n")
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        sys.stderr.write(f"CLI measurement error (benchmark aborted; report not updated): {exc}\n")
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
                *config_args,
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
                    *config_args,
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
    adapter_stats = compute_stats(batch_timings)
    cold_stats = compute_stats([record["elapsed_ns"] for record in cold_records])

    input_lengths = [len(c.address) for c in cases]
    input_len_summary = {
        "min": min(input_lengths) if input_lengths else 0,
        "max": max(input_lengths) if input_lengths else 0,
        "mean": sum(input_lengths) / len(input_lengths) if input_lengths else 0.0,
    }

    report = {
        "schema_version": 2,
        "metadata": {
            "measured_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "config": asdict(load_config(config_path)),
            "dictionary_path": str(dict_path),
            "dictionary_hash": sha256_file(dict_path),
            "cases_path": str(cases_path),
            "cases_hash": sha256_file(cases_path),
            "source_hash": get_source_hash(),
            "benchmark_source_hash": sha256_file(Path(__file__)),
            "platform": get_platform_metadata(),
            "processes": args.processes,
            "warmup": args.warmup,
            "repetitions": args.repetitions,
            "seed": args.seed,
            "cli_timeout_seconds": args.cli_timeout,
            "total_cases": len(cases),
            "dictionary_entries": {key: len(values) for key, values in dict_data.items()},
            "input_lengths": input_len_summary,
        },
        "gc_policy": {
            "is_enabled": gc.isenabled(),
            "thresholds": list(gc.get_threshold()),
            "counts": list(gc.get_count()),
        },
        "phases": {
            "cold_process_request": cold_stats,
            "cold_process_exit": compute_stats([record["process_exit_ns"] for record in cold_records]),
            "fresh_process_import": import_stats,
            "interpreter_and_import_total": interpreter_and_import_stats,
            "dictionary_load": load_stats,
            "matcher_index": index_stats,
            "first_request": first_request_stats,
            "warmed_request": warmed_stats,
            "child_total_elapsed": child_elapsed_stats,
            "full_jsonl_adapter": adapter_stats,
        },
        "measurement_contract": {
            "primary": "cold_process_request",
            "timer": "parent perf_counter_ns immediately before Popen until the complete JSONL result line is read from stdout",
            "command": adapter_command,
            "includes": ["process launch", "interpreter startup", "imports", "dictionary/config I/O",
                         "index construction", "stdin pipe transfer", "input JSON parsing",
                         "identify", "output JSON serialization", "stdout pipe capture/flush"],
            "excludes": ["harness fixture loading", "harness input encoding", "harness output validation/scoring",
                         "report file writes", "memory tracing", "process teardown after output"],
            "stdout": "UTF-8 JSONL captured through an OS pipe; no prediction file or terminal rendering",
            "cold_definition": "fresh interpreter and matcher for every address; no persistent index or prediction cache",
            "filesystem_cache": "OS file/page caches and Python bytecode caches are uncontrolled; not a cold-machine measurement",
            "stdout_observer": "parent reader thread timestamps complete lines; pipe/thread setup precedes timing; scheduling overhead is included",
            "process_exit": "cold_process_exit separately records launch through communicate/wait; every CLI must exit successfully",
            "first_request": "identify only, after index construction; diagnostic, excludes startup",
            "child_total_elapsed": "diagnostic worker including case loading, warmups and report-file I/O; not grading latency",
            "batch": "one process for the entire dataset; elapsed / case count is throughput, not maximum request latency",
        },
        "cold_coverage": {
            "selection": "all" if len(selected_cases) == len(cases) else "seeded_uniform_sample_without_replacement",
            "selected_case_ids": [case.id for case in selected_cases],
            "unique_cases": len(selected_cases),
            "total_cases": len(cases),
            "full_dataset": len(selected_cases) == len(cases),
            "rounds": args.processes,
            "warmup": 0,
            "matches_expected": sum(record["matches_expected"] for record in cold_records),
        },
        "batch_process": {
            "cases_per_process": len(cases),
            "amortized_per_case": compute_stats([ns / len(cases) for ns in batch_timings]),
            "process_exit": compute_stats(batch_exit_timings),
            "raw_elapsed_ns": batch_timings,
            "raw_process_exit_ns": batch_exit_timings,
            "matches_expected_per_round": batch_matches,
        },
        "memory": mem_result,
        "thresholds": {
            "scope": "cold_process_request",
            "mean_request_ms_target": 10.0,
            "max_request_ms_target": 100.0,
            "observed_warmed_mean_ms": warmed_stats["mean_ms"],
            "observed_warmed_max_ms": warmed_stats["max_ms"],
            "observed_first_request_max_ms": first_request_stats["max_ms"],
            "observed_mean_ms": cold_stats["mean_ms"],
            "observed_overall_max_ms": cold_stats["max_ms"],
            "mean_target_met": (cold_stats["mean_ms"] <= 10.0),
            "max_target_met": (cold_stats["max_ms"] <= 100.0),
            "warm_max_target_met": (warmed_stats["max_ms"] <= 100.0),
            "all_requests_within_target": (
                cold_stats["max_ms"] <= 100.0
            ),
            "cold_request_case_ids": [case.id for case in selected_cases],
            "first_identify_case_ids": first_case_ids,
            "observation_note": (
                "Local observed cold-process limits only; sampled runs cannot certify full-dataset mean or maximum. "
                "Primary timing stops when the parent receives the complete result line; process exit is measured separately."
            ),
        },
        "raw_cold_process_timings": cold_records,
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

    print(f"Cold process per address: mean {cold_stats['mean_ms']:.3f} ms / 10 ms; "
          f"max {cold_stats['max_ms']:.3f} ms / 100 ms. "
          f"Coverage: {len(selected_cases)}/{len(cases)} cases x {args.processes} rounds. "
          f"Mean {'PASS' if report['thresholds']['mean_target_met'] else 'FAIL'}, "
          f"max {'PASS' if report['thresholds']['max_target_met'] else 'FAIL'}.")
    return 0


if __name__ == "__main__":
    sys.exit(run_benchmark())
