"""Measure a prepared standalone bundle from launch through complete stdout."""

import argparse
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import time

from address_segmentation.io import load_dictionary
from tools.benchmark import compute_stats, measure_cli
from tools.datasets import canonical_json_hash, get_platform_metadata, load_cases, sha256_file


def benchmark_prepared(bundle, dictionary_path, cases_path, *, processes=5, cold_cases=0,
                       seed=42, no_site=False, source_only=False, timeout=30):
    if processes <= 0 or cold_cases < 0 or not 0 < timeout < float("inf"):
        raise ValueError("processes and timeout must be positive; cold-cases must be non-negative")
    bundle = Path(bundle).resolve()
    data = load_dictionary(str(dictionary_path))
    cases = load_cases(cases_path, data)
    if not cases:
        raise ValueError("Cases dataset cannot be empty")
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    if manifest["metadata"]["dictionary_hash"] != canonical_json_hash(data):
        raise ValueError("Benchmark dictionary does not match the compiled dictionary")
    # Verify build identities outside timing; original dictionary and fixtures are
    # harness inputs only. The measured standalone process cannot access them.
    for filename, expected in manifest["files"].items():
        if sha256_file(bundle / filename) != expected["sha256"]:
            raise ValueError(f"Bundle file hash mismatch: {filename}")
    selected = (random.Random(seed).sample(cases, cold_cases)
                if 0 < cold_cases < len(cases) else list(cases))
    records, batches = [], []
    with tempfile.TemporaryDirectory(prefix="prepared-source-only-") as temporary:
        execution_bundle = bundle
        if source_only:
            execution_bundle = Path(temporary)
            for filename in ("submission.py", "matcher_runtime.py", "dictionary.idx"):
                shutil.copyfile(bundle / filename, execution_bundle / filename)
        command = [sys.executable, *(["-S"] if no_site else []),
                   *(["-B"] if source_only else []), str(execution_bundle / "submission.py")]
        for round_index in range(processes):
            order = list(selected)
            random.Random(seed + round_index * 1000).shuffle(order)
            for case in order:
                elapsed, exit_ns, predictions = measure_cli(command, [case.address], dict(os.environ), timeout)
                records.append({"case_id": case.id, "round": round_index,
                                "input_len": len(case.address), "elapsed_ns": elapsed,
                                "process_exit_ns": exit_ns, "prediction": predictions[0],
                                "matches_expected": predictions[0] == case.expected})
            elapsed, exit_ns, predictions = measure_cli(command, [c.address for c in cases], dict(os.environ), timeout)
            batches.append({"elapsed_ns": elapsed, "process_exit_ns": exit_ns,
                            "matches_expected": sum(p == c.expected for p, c in zip(predictions, cases))})
            print(f"Prepared round {round_index + 1}/{processes}: {len(order)} fresh processes + one batch complete",
                  file=sys.stderr, flush=True)
    stats = compute_stats([record["elapsed_ns"] for record in records])
    return {
        "schema_version": 1,
        "metadata": {
            "measured_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "platform": get_platform_metadata(), "bundle": str(bundle),
            "manifest": manifest, "dictionary_file_hash": sha256_file(dictionary_path),
            "cases_hash": sha256_file(cases_path), "harness_hash": sha256_file(__file__),
            "processes": processes, "seed": seed, "no_site": no_site, "source_only": source_only,
            "cli_timeout_seconds": timeout, "total_cases": len(cases),
        },
        "measurement_contract": {
            "primary": "cold_process_request",
            "timer": "parent perf_counter_ns immediately before Popen until complete stdout JSONL result is received",
            "command": command,
            "includes": ["process/interpreter startup", "all runtime imports", "prepared file open/mapping and header checks",
                         "on-demand dictionary/postings reads", "stdin transfer/JSON decoding", "matching",
                         "JSON encoding and stdout flush/pipe capture"],
            "excludes": ["offline dictionary compilation", "harness input setup and fixture parsing",
                         "harness bundle hash checks", "output validation/scoring", "report file writes",
                         "process teardown after output"],
            "cold_definition": "fresh interpreter and matcher for every address; zero cold warmups; no result cache or persistent worker",
            "filesystem_cache": "OS file/page caches are uncontrolled; harness preflight reads bundle files",
            "bytecode": ("absent; -B prevents every measured process from creating it" if source_only
                         else "checked-hash runtime bytecode is shipped in the prepared bundle; all source is also included"),
            "source_only_command_note": "source-only commands refer to a temporary copy removed after measurement",
            "site": "site initialization disabled explicitly with -S" if no_site else "normal interpreter site initialization",
            "protocol": "UTF-8 JSON strings on stdin, three-field JSON objects captured from stdout; no prediction file",
            "process_exit": "separately recorded; every child must also exit successfully within the timeout",
        },
        "cold_coverage": {
            "selection": "all" if len(selected) == len(cases) else "seeded_uniform_sample_without_replacement",
            "selected_case_ids": [c.id for c in selected], "unique_cases": len(selected),
            "total_cases": len(cases), "full_dataset": len(selected) == len(cases),
            "rounds": processes, "warmup": 0,
            "matches_expected": sum(r["matches_expected"] for r in records),
        },
        "phases": {"cold_process_request": stats,
                   "cold_process_exit": compute_stats([r["process_exit_ns"] for r in records])},
        "thresholds": {"scope": "cold_process_request", "mean_request_ms_target": 10,
                       "max_request_ms_target": 100, "mean_target_met": stats["mean_ms"] <= 10,
                       "max_target_met": stats["max_ms"] <= 100},
        "batch_process": {"cases_per_process": len(cases), "records": batches,
                          "note": "Separate throughput diagnostic; never substituted for cold latency"},
        "raw_cold_process_timings": records,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--dictionary", required=True, help="Harness-only dictionary identity and label validation")
    parser.add_argument("--cases", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--processes", type=int, default=5)
    parser.add_argument("--cold-cases", type=int, default=0, help="0 measures all cases; N selects a seeded uniform sample")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--no-site", action="store_true", help="Explicitly launch the measured interpreter with -S")
    parser.add_argument("--source-only", action="store_true", help="Omit bytecode in an isolated copy and use -B")
    parser.add_argument("--cli-timeout", type=float, default=30)
    args = parser.parse_args(argv)
    if args.processes <= 0 or args.cold_cases < 0 or not 0 < args.cli_timeout < float("inf"):
        parser.error("processes and timeout must be positive; cold-cases must be non-negative")
    try:
        report = benchmark_prepared(args.bundle, args.dictionary, args.cases, processes=args.processes,
                                    cold_cases=args.cold_cases, seed=args.seed, no_site=args.no_site,
                                    source_only=args.source_only, timeout=args.cli_timeout)
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Prepared benchmark error (report not updated): {exc}\n")
    stats, limits = report["phases"]["cold_process_request"], report["thresholds"]
    print(f"Prepared cold process per address: mean {stats['mean_ms']:.3f} ms / 10 ms "
          f"({'PASS' if limits['mean_target_met'] else 'FAIL'}); "
          f"max {stats['max_ms']:.3f} ms / 100 ms ({'PASS' if limits['max_target_met'] else 'FAIL'}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
