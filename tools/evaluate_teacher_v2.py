"""Load and score the teacher's v2 data without modifying the supplied files."""

import argparse
from contextlib import nullcontext
import json
from pathlib import Path

from address_segmentation import AddressMatcher
from address_segmentation.catalog import validate_field_sequence
from address_segmentation.prepared import PreparedMatcher
from tools.datasets import (
    EvaluationCase, canonical_json_hash, get_platform_metadata, get_source_hash, sha256_bytes, sha256_file,
)
from tools.evaluate import EVALUATION_FIELDS, make_rate


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key!r}")
        result[key] = value
    return result


def load_teacher_v2(source_dir):
    """Return constructor lists, ordered cases, and provenance from the original files."""
    source = Path(source_dir)
    raw = {name: (source / name).read_bytes() for name in (
        "public.json", *(f"public_list_{field}.txt" for field in EVALUATION_FIELDS)
    )}
    inventories = {
        field: validate_field_sequence(raw[f"public_list_{field}.txt"].decode("utf-8").strip().splitlines(), field)
        for field in EVALUATION_FIELDS
    }
    membership = {field: set(labels) for field, labels in inventories.items()}
    data = json.loads(raw["public.json"], object_pairs_hook=_unique_object)
    if not isinstance(data, list) or not data:
        raise ValueError("public.json must contain a nonempty list")

    cases, seen = [], {}
    for row, record in enumerate(data, 1):
        if not isinstance(record, dict) or record.keys() != {"address", "result"}:
            raise ValueError(f"Row {row}: expected address and result")
        address, expected = record["address"], record["result"]
        if not isinstance(address, str) or not address.strip():
            raise ValueError(f"Row {row}: expected a nonblank address")
        if not isinstance(expected, dict) or expected.keys() != membership.keys():
            raise ValueError(f"Row {row}: invalid result fields")
        for field, label in expected.items():
            if label is not None and (not isinstance(label, str) or label not in membership[field]):
                raise ValueError(f"Row {row}: {field} label missing from dictionary: {label!r}")
        if address in seen:
            kind = "Duplicate" if seen[address] == expected else "Conflicting"
            raise ValueError(f"{kind} address in row {row}: {address!r}")
        seen[address] = expected
        cid = str(row)
        kind = "unsupported" if None in expected.values() else "recoverable"
        cases.append(EvaluationCase(cid, address, expected, ("teacher-v2-public",), cid, kind))

    return inventories, cases, {
        "dataset": "teacher-v2-public",
        "official_data": True,
        "source_dir": str(source),
        "source_hashes": {name: sha256_bytes(data) for name, data in raw.items()},
        "dictionary_counts": {field: len(labels) for field, labels in inventories.items()},
        "case_count": len(cases),
    }


def score_teacher_v2(cases, predictions, exceptions):
    """Apply the notebook's exact equality and zero-points-on-exception rules."""
    counts = dict.fromkeys(EVALUATION_FIELDS, 0)
    failures, correct_cases = [], []
    for case in cases:
        error = exceptions.get(case.id)
        actual = None if error else predictions[case.id]
        wrong = list(EVALUATION_FIELDS) if error else [
            field for field in EVALUATION_FIELDS if actual[field] != case.expected[field]
        ]
        for field in counts:
            counts[field] += field not in wrong
        if wrong:
            failures.append({"id": case.id, "address": case.address, "expected": case.expected,
                             "predicted": actual, "wrong_fields": wrong, "exception": error})
        else:
            correct_cases.append(case.id)
    correct, total = sum(counts.values()), len(cases) * len(counts)
    return {
        "correct_fields": correct,
        "total_fields": total,
        "score_10": round(correct / total * 10, 2),
        "per_field_accuracy": {field: make_rate(n, len(cases)) for field, n in counts.items()},
        "complete_accuracy": make_rate(len(correct_cases), len(cases)),
        "correct_cases": correct_cases,
        "failures": failures,
        "exception_count": len(exceptions),
    }


def compare_teacher_v2(cases, metadata, metrics, baseline):
    if baseline["metadata"]["source_hashes"] != metadata["source_hashes"]:
        raise ValueError("Baseline data hashes differ")
    before = {row["id"]: set(row["wrong_fields"]) for row in baseline["metrics"]["failures"]}
    after = {row["id"]: set(row["wrong_fields"]) for row in metrics["failures"]}
    gains, regressions = [], []
    for case in cases:
        old, new = before.get(case.id, set()), after.get(case.id, set())
        if old - new:
            gains.append({"id": case.id, "address": case.address, "fields": sorted(old - new)})
        if new - old:
            regressions.append({"id": case.id, "address": case.address, "fields": sorted(new - old)})
    return {"gains": gains, "regressions": regressions,
            "complete_address_gains": sorted(set(metrics["correct_cases"]) - set(baseline["metrics"]["correct_cases"]), key=int),
            "complete_address_regressions": sorted(set(baseline["metrics"]["correct_cases"]) - set(metrics["correct_cases"]), key=int)}


def validate_result(result):
    if (not isinstance(result, dict) or result.keys() != set(EVALUATION_FIELDS)
            or any(value is not None and not isinstance(value, str) for value in result.values())):
        raise ValueError(f"Invalid result: {result!r}")


def audit_hierarchy(hierarchy, cases):
    all_streets = {street for wards in hierarchy.values() for streets in wards.values() for street in streets}
    checked_pairs = checked_paths = covered_pairs = covered_paths = 0
    missing_paths = []
    for case in cases:
        province, ward, street = (case.expected[field] for field in ("province", "ward", "street"))
        if province is None or ward is None:
            continue
        streets = hierarchy.get(province, {}).get(ward)
        checked_pairs += 1
        covered_pairs += streets is not None
        if street is None:
            continue
        checked_paths += 1
        if streets is not None and street in streets:
            covered_paths += 1
        else:
            reason = "missing_province_ward" if streets is None else (
                "street_found_in_other_area" if street in all_streets else "street_unmapped"
            )
            missing_paths.append({"id": case.id, "expected": case.expected, "reason": reason})
    return {"province_ward": make_rate(covered_pairs, checked_pairs),
            "complete_path": make_rate(covered_paths, checked_paths),
            "missing_paths": missing_paths}


def write_report(path, report):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=Path("teacher-info/v2_2_level_address"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--prepared-index", type=Path, help="Evaluate a compiled dictionary.idx")
    parser.add_argument("--hierarchy", type=Path, help="Also audit coverage of a hierarchy JSON")
    args = parser.parse_args()

    lists, cases, metadata = load_teacher_v2(args.source_dir)
    dictionary = {field + "s": labels for field, labels in lists.items()}
    hierarchy = None
    if args.hierarchy:
        raw_hierarchy = args.hierarchy.read_bytes()
        hierarchy = json.loads(raw_hierarchy, object_pairs_hook=_unique_object)
        metadata["hierarchy_sha256"] = sha256_bytes(raw_hierarchy)
    predictions, exceptions = {}, {}
    matching = PreparedMatcher(args.prepared_index) if args.prepared_index else nullcontext(AddressMatcher(**dictionary))
    with matching as matcher:
        if args.prepared_index:
            index_metadata = matcher._store.metadata
            if index_metadata["dictionary_hash"] != canonical_json_hash(dictionary):
                parser.error("Prepared index and teacher inventories differ")
            if hierarchy is not None and index_metadata.get("hierarchy_hash") != canonical_json_hash(hierarchy):
                parser.error("Prepared index and hierarchy differ")
            metadata["prepared_index_sha256"] = sha256_file(args.prepared_index)
        for case in cases:
            try:
                result = matcher.identify(case.address)
                validate_result(result)
                predictions[case.id] = result
            except Exception as error:
                exceptions[case.id] = f"{type(error).__name__}: {error}"
    metrics = score_teacher_v2(cases, predictions, exceptions)
    metadata.update(platform=get_platform_metadata(), source_hash=get_source_hash())
    report = {"metadata": metadata, "metrics": metrics, "predictions": predictions, "exceptions": exceptions}
    if hierarchy is not None:
        report["hierarchy_coverage"] = audit_hierarchy(hierarchy, cases)
    if args.baseline:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        report["baseline_comparison"] = compare_teacher_v2(cases, metadata, metrics, baseline)
    write_report(args.output, report)
    print(f'{metrics["correct_fields"]}/{metrics["total_fields"]} fields, '
          f'{metrics["score_10"]:.2f}/10, {metrics["exception_count"]} exceptions')
    return bool(exceptions)


if __name__ == "__main__":
    raise SystemExit(main())
