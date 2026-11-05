"""Adapt user fixtures to the development evaluator without changing their source.

This explicit adapter accepts all_raw.json (address/result records) and the three
list_*.txt files. It does not define or guess the instructor's eventual protocol.
Reviewed accent collisions are applied only through an optional audit file.
"""

import argparse
import hashlib
import json
from pathlib import Path

from address_segmentation.catalog import Catalog
from address_segmentation.normalize import fold_accents
from tools.datasets import canonical_json_hash, load_cases, sha256_file

FIELDS = ("street", "ward", "province")


def prepare_dataset(source_dir: Path, output_dir: Path, review: Path | None = None) -> dict:
    source_dir, output_dir = Path(source_dir).resolve(), Path(output_dir).resolve()
    if source_dir == output_dir or source_dir in output_dir.parents:
        raise ValueError("Output must be outside the raw source directory")
    paths = {field + "s": source_dir / f"list_{field}.txt" for field in FIELDS}
    paths["raw"] = source_dir / "all_raw.json"
    dictionary = {key: path.read_text(encoding="utf-8").splitlines()
                  for key, path in paths.items() if key != "raw"}
    Catalog(**dictionary)  # Validate every label; never strip or rewrite canonical data.
    raw = json.loads(paths["raw"].read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise ValueError("all_raw.json must contain a nonempty array")
    cases = []
    seen = {}
    for number, record in enumerate(raw, 1):
        if not isinstance(record, dict) or set(record) != {"address", "result"}:
            raise ValueError(f"Record {number}: expected exactly address/result")
        address, expected = record["address"], record["result"]
        if not isinstance(address, str) or not isinstance(expected, dict):
            raise ValueError(f"Record {number}: address must be text and result an object")
        if set(expected) != set(FIELDS):
            raise ValueError(f"Record {number}: result must contain exactly {FIELDS}")
        for field, label in expected.items():
            if label is not None and (not isinstance(label, str) or label not in dictionary[field + "s"]):
                raise ValueError(f"Record {number}: invalid canonical {field}: {label!r}")
        if address in seen and seen[address] != expected:
            raise ValueError(f"Record {number}: duplicate input has conflicting expectations")
        seen[address] = expected
        # Repeated identical addresses share a group; IDs retain source row numbers.
        group = hashlib.sha256(address.encode("utf-8")).hexdigest()[:16]
        cases.append(dict(id=f"user-{source_dir.name}-{number:04d}", address=address, expected=dict(expected),
                          tags=["user-supplied"], group_id=f"address-{group}",
                          expectation_kind="recoverable" if any(v is not None for v in expected.values()) else "unsupported"))
    raw_cases = [dict(case, expected=dict(case["expected"]), tags=list(case["tags"])) for case in cases]
    corrections = json.loads(Path(review).read_text(encoding="utf-8")) if review else []
    if not isinstance(corrections, list):
        raise ValueError("Review must contain an array of accent-collision records")
    reviewed = set()
    for item in corrections:
        if not isinstance(item, dict) or set(item) != {"record", "field", "original", "alternatives", "reason"}:
            raise ValueError("Invalid review record shape")
        number, field = item["record"], item["field"]
        if (type(number) is not int or not 1 <= number <= len(cases)
                or not isinstance(field, str) or field not in FIELDS or (number, field) in reviewed):
            raise ValueError("Invalid or repeated review record/field")
        reviewed.add((number, field))
        case = cases[number - 1]
        alternatives = item["alternatives"]
        if (not isinstance(alternatives, list) or len(alternatives) < 2
                or any(not isinstance(v, str) or v not in dictionary[field + "s"] for v in alternatives)
                or len(set(alternatives)) != len(alternatives)
                or len({fold_accents(v) for v in alternatives}) != 1
                or item["original"] != case["expected"][field]
                or item["original"] not in alternatives
                or not isinstance(item["reason"], str) or not item["reason"].strip()):
            raise ValueError(f"Record {number}: review must document a canonical accent collision")
        case["expected"][field] = None
        case["expectation_kind"] = "ambiguous"
        if "reviewed-accent-collision" not in case["tags"]:
            case["tags"].append("reviewed-accent-collision")
    manifest = {
        "adapter": "user-address-result-v1", "official_data": False,
        "source_hashes": {path.name: sha256_file(path) for path in paths.values()},
        "review_hash": sha256_file(review) if review else None,
        "counts": {**{key: len(value) for key, value in dictionary.items()}, "cases": len(cases)},
        "reviewed_fields": len(corrections),
        "content_hashes": {"dictionary": canonical_json_hash(dictionary),
                           "raw_cases": canonical_json_hash(raw_cases), "cases": canonical_json_hash(cases)},
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "dictionary.json").write_text(json.dumps(dictionary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for name, records in (("raw-cases.jsonl", raw_cases), ("cases.jsonl", cases)):
        target = output_dir / name
        target.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records), encoding="utf-8")
        load_cases(target, dictionary)
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--review", type=Path)
    args = parser.parse_args()
    try:
        prepare_dataset(args.source_dir, args.output_dir, args.review)
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(1, f"Dataset preparation failed: {exc}\n")


if __name__ == "__main__":
    main()
