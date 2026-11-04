"""Evaluation harness and scoring command for address segmentation.

Calculates pure metric rates, false non-null, wrong label, missed non-null,
ambiguity abstention, per-case failure analysis, baseline comparison, and
dataset hash verification with zero stdout debug output.
"""

import argparse
from dataclasses import asdict
from collections import Counter
from collections.abc import Sequence
import json
from pathlib import Path
import sys
from typing import Any

from address_segmentation.io import load_dictionary, load_config
from address_segmentation.matcher import AddressMatcher
from tools.datasets import (
    EvaluationCase,
    get_platform_metadata,
    get_source_hash,
    load_cases,
    sha256_file,
)

EVALUATION_FIELDS: tuple[str, ...] = ("street", "ward", "province")


def make_rate(numerator: int, denominator: int) -> dict[str, Any]:
    """Construct a metric dictionary with numerator, denominator, and rate (None if den=0)."""
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": (numerator / denominator) if denominator > 0 else None,
    }


def get_matcher_config(matcher: Any) -> dict[str, Any]:
    return asdict(matcher.config)


def extract_matcher_trace(matcher: Any, address: str) -> dict[str, Any]:
    return matcher.identify_with_trace(address)[1]


def score_retrieval(cases, predictions, traces):
    counts = {stage: 0 for stage in ("retrieved", "verified", "retained")}
    total = 0
    failures = []
    for case in cases:
        if case.expectation_kind != "recoverable":
            continue
        trace = traces[case.id]
        for field, label in case.expected.items():
            if label is None:
                continue
            total += 1
            first_loss = None
            for stage in counts:
                found = any(c["field"] == field and c["label"] == label for c in trace[stage])
                counts[stage] += int(found)
                if not found and first_loss is None:
                    first_loss = stage
            if predictions[case.id][field] != label:
                failures.append({"id": case.id, "field": field,
                                 "first_loss": first_loss or "selection"})
    return {"status": "available", "recall_before_verification": make_rate(counts["retrieved"], total),
            "recall_after_verification": make_rate(counts["verified"], total),
            "recall_after_top_k": make_rate(counts["retained"], total), "failures": failures,
            "budget_exhausted_cases": [cid for cid, trace in traces.items() if trace.get("budgets", {}).get("exhausted")]}


def validate_prediction_mapping(
    cases: Sequence[EvaluationCase | dict[str, Any]],
    predictions: Any,
) -> dict[str, dict[str, str | None]]:
    """Strictly validate that predictions contain exactly one valid prediction per case.

    Rejects missing IDs, extra IDs, short/long sequences, duplicate IDs,
    missing/extra keys, and non-string/non-null values.
    """
    expected_ids: list[str] = []
    seen_expected_ids: set[str] = set()
    for idx, c in enumerate(cases):
        cid = c["id"] if isinstance(c, dict) else c.id
        if cid in seen_expected_ids:
            raise ValueError(f"Duplicate case id '{cid}' in cases at index {idx}")
        seen_expected_ids.add(cid)
        expected_ids.append(cid)

    if isinstance(predictions, dict):
        if "predictions" in predictions and isinstance(predictions["predictions"], dict):
            raw_map = predictions["predictions"]
        else:
            raw_map = predictions

        pred_keys = set(raw_map.keys())
        missing_ids = seen_expected_ids - pred_keys
        if missing_ids:
            raise ValueError(
                f"Predictions missing {len(missing_ids)} case(s), e.g. '{sorted(missing_ids)[0]}'"
            )
        extra_ids = pred_keys - seen_expected_ids
        if extra_ids:
            raise ValueError(
                f"Predictions contain {len(extra_ids)} extra id(s), e.g. '{sorted(extra_ids)[0]}'"
            )
        pred_map = raw_map

    elif isinstance(predictions, (list, tuple)):
        if len(predictions) != len(cases):
            raise ValueError(
                f"Predictions sequence length {len(predictions)} does not match cases count {len(cases)}"
            )

        has_ids = all(isinstance(p, dict) and "id" in p for p in predictions)
        if has_ids:
            pred_map = {}
            seen_pids: set[str] = set()
            for p in predictions:
                pid = p["id"]
                if pid in seen_pids:
                    raise ValueError(f"Duplicate prediction id '{pid}' in prediction list")
                seen_pids.add(pid)
                if pid not in seen_expected_ids:
                    raise ValueError(f"Extra prediction id '{pid}' not in cases")
                pred_map[pid] = p

            missing_ids = seen_expected_ids - seen_pids
            if missing_ids:
                raise ValueError(
                    f"Predictions missing {len(missing_ids)} case(s), e.g. '{sorted(missing_ids)[0]}'"
                )
        else:
            pred_map = {}
            for idx, p in enumerate(predictions):
                cid = expected_ids[idx]
                pred_map[cid] = p
    else:
        raise TypeError(
            f"predictions must be a dict or sequence, got {type(predictions).__name__}"
        )

    # Validate each prediction object strictly
    validated_map: dict[str, dict[str, str | None]] = {}
    required_field_keys = frozenset(EVALUATION_FIELDS)
    for cid in expected_ids:
        pred_obj = pred_map[cid]
        if not isinstance(pred_obj, dict):
            raise TypeError(
                f"Prediction for case '{cid}' must be a dict, got {type(pred_obj).__name__}"
            )

        actual_keys = frozenset(k for k in pred_obj.keys() if k != "id")
        if actual_keys != required_field_keys:
            raise ValueError(
                f"Prediction for case '{cid}' field keys must be exactly {sorted(required_field_keys)}, got {sorted(actual_keys)}"
            )

        validated_pred: dict[str, str | None] = {}
        for f in EVALUATION_FIELDS:
            val = pred_obj[f]
            if val is not None and not isinstance(val, str):
                raise TypeError(
                    f"Prediction for case '{cid}' field '{f}' must be str or None, got {type(val).__name__}"
                )
            validated_pred[f] = val
        validated_map[cid] = validated_pred

    return validated_map


def score_cases(
    cases: Sequence[EvaluationCase | dict[str, Any]],
    predictions: Any,
) -> dict[str, Any]:
    """Pure reusable scoring function evaluating predictions against ground-truth cases.

    Returns complete accuracy, per-field accuracy, false non-null rates among expected nulls,
    wrong label rates among expected non-nulls, missed non-null rates, missed recoverable rates,
    ambiguity abstention rates, category counts, per-case failure details, and unavailable retrieval metrics.
    All rates carry numerator, denominator, and rate=None when denominator is zero.
    Requires exactly one valid prediction per case.
    """
    pred_map = validate_prediction_mapping(cases, predictions)

    total_cases = len(cases)
    matched_complete_count = 0

    field_matches: Counter[str] = Counter()

    false_non_null_counts: Counter[str] = Counter()
    expected_null_counts: Counter[str] = Counter()

    wrong_label_counts: Counter[str] = Counter()
    missed_nonnull_counts: Counter[str] = Counter()
    expected_nonnull_counts: Counter[str] = Counter()

    missed_recoverable_counts: Counter[str] = Counter()
    recoverable_nonnull_counts: Counter[str] = Counter()

    ambig_cases_count = 0
    ambig_cases_abstained_count = 0
    ambig_expected_null_field_count = 0
    ambig_null_abstained_field_count = 0

    kind_counts: Counter[str] = Counter()
    tag_counts: Counter[str] = Counter()

    per_case_failures: list[dict[str, Any]] = []

    for case in cases:
        cid = case["id"] if isinstance(case, dict) else case.id
        addr = case["address"] if isinstance(case, dict) else case.address
        exp = case["expected"] if isinstance(case, dict) else case.expected
        tags = case["tags"] if isinstance(case, dict) else case.tags
        kind = case["expectation_kind"] if isinstance(case, dict) else case.expectation_kind

        kind_counts[kind] += 1
        for t in tags:
            tag_counts[t] += 1

        pred = pred_map[cid]

        case_is_complete = True
        field_errors: dict[str, dict[str, Any]] = {}

        for f in EVALUATION_FIELDS:
            exp_val = exp.get(f)
            pred_val = pred.get(f)

            if exp_val == pred_val:
                field_matches[f] += 1
            else:
                case_is_complete = False

                if exp_val is None:
                    error_type = "false_non_null"
                elif pred_val is None:
                    error_type = "missed_nonnull"
                else:
                    error_type = "wrong_label"

                field_errors[f] = {
                    "expected": exp_val,
                    "predicted": pred_val,
                    "error_type": error_type,
                }

            if exp_val is None:
                expected_null_counts[f] += 1
                if pred_val is not None:
                    false_non_null_counts[f] += 1
            else:
                expected_nonnull_counts[f] += 1
                if pred_val is None:
                    missed_nonnull_counts[f] += 1
                elif pred_val != exp_val:
                    wrong_label_counts[f] += 1

                if kind == "recoverable":
                    recoverable_nonnull_counts[f] += 1
                    if pred_val is None:
                        missed_recoverable_counts[f] += 1

        if case_is_complete:
            matched_complete_count += 1
        else:
            per_case_failures.append(
                {
                    "id": cid,
                    "address": addr,
                    "expected": dict(exp),
                    "predicted": {f: pred.get(f) for f in EVALUATION_FIELDS},
                    "expectation_kind": kind,
                    "tags": list(tags),
                    "field_errors": field_errors,
                }
            )

        if kind == "ambiguous":
            ambig_cases_count += 1
            ambig_case_abstained = True
            for f in EVALUATION_FIELDS:
                if exp.get(f) is None:
                    ambig_expected_null_field_count += 1
                    if pred.get(f) is None:
                        ambig_null_abstained_field_count += 1
                    else:
                        ambig_case_abstained = False
            if ambig_case_abstained:
                ambig_cases_abstained_count += 1

    total_field_slots = total_cases * len(EVALUATION_FIELDS)
    total_field_matches = sum(field_matches.values())

    per_field_accuracy = {
        "overall": make_rate(total_field_matches, total_field_slots),
        "by_field": {
            f: make_rate(field_matches[f], total_cases) for f in EVALUATION_FIELDS
        },
    }

    total_exp_nulls = sum(expected_null_counts.values())
    total_false_non_nulls = sum(false_non_null_counts.values())
    false_non_null = {
        "overall": make_rate(total_false_non_nulls, total_exp_nulls),
        "by_field": {
            f: make_rate(false_non_null_counts[f], expected_null_counts[f])
            for f in EVALUATION_FIELDS
        },
    }

    total_exp_nonnulls = sum(expected_nonnull_counts.values())
    total_wrong_labels = sum(wrong_label_counts.values())
    wrong_label = {
        "overall": make_rate(total_wrong_labels, total_exp_nonnulls),
        "by_field": {
            f: make_rate(wrong_label_counts[f], expected_nonnull_counts[f])
            for f in EVALUATION_FIELDS
        },
    }

    total_missed_nonnulls = sum(missed_nonnull_counts.values())
    missed_nonnull = {
        "overall": make_rate(total_missed_nonnulls, total_exp_nonnulls),
        "by_field": {
            f: make_rate(missed_nonnull_counts[f], expected_nonnull_counts[f])
            for f in EVALUATION_FIELDS
        },
    }

    total_rec_nonnulls = sum(recoverable_nonnull_counts.values())
    total_rec_missed = sum(missed_recoverable_counts.values())
    missed_recoverable = {
        "overall": make_rate(total_rec_missed, total_rec_nonnulls),
        "by_field": {
            f: make_rate(missed_recoverable_counts[f], recoverable_nonnull_counts[f])
            for f in EVALUATION_FIELDS
        },
    }

    ambiguity_abstention = {
        "case_level": make_rate(ambig_cases_abstained_count, ambig_cases_count),
        "field_level": make_rate(
            ambig_null_abstained_field_count, ambig_expected_null_field_count
        ),
    }

    return {
        "complete_accuracy": make_rate(matched_complete_count, total_cases),
        "per_field_accuracy": per_field_accuracy,
        "false_non_null": false_non_null,
        "wrong_label": wrong_label,
        "missed_nonnull": missed_nonnull,
        "missed_recoverable": missed_recoverable,
        "ambiguity_abstention": ambiguity_abstention,
        "category_counts": {
            "total_cases": total_cases,
            "by_expectation_kind": dict(kind_counts),
            "by_tag": dict(tag_counts),
        },
        "per_case_failures": per_case_failures,
        "retrieval": {
            "status": "unavailable",
            "message": "Matcher trace API not yet available in Stage B",
            "recall_before_verification": None,
            "recall_after_verification": None,
            "recall_after_top_k": None,
        },
    }


def compare_baseline(
    cases: Sequence[EvaluationCase | dict[str, Any]],
    current_predictions: Any,
    baseline_report: dict[str, Any],
    current_dict_hash: str,
    current_cases_hash: str,
) -> dict[str, Any]:
    """Compare current predictions against a baseline evaluation report by case IDs.

    Verifies identical dictionary and cases dataset hashes before computing gains and regressions.
    Validates prediction mappings strictly for both current and baseline predictions.
    """
    if not isinstance(baseline_report, dict):
        raise TypeError(
            f"baseline_report must be a dict, got {type(baseline_report).__name__}"
        )

    base_meta = baseline_report.get("metadata", {})
    base_dict_hash = base_meta.get("dictionary_hash")
    base_cases_hash = base_meta.get("cases_hash")

    if base_dict_hash != current_dict_hash or base_cases_hash != current_cases_hash:
        raise ValueError(
            f"Baseline dataset hash mismatch: "
            f"dictionary ({base_dict_hash} vs {current_dict_hash}), "
            f"cases ({base_cases_hash} vs {current_cases_hash})"
        )

    base_preds_raw = baseline_report.get("predictions")
    if base_preds_raw is None:
        raise ValueError("baseline_report missing 'predictions' section")

    curr_p_map = validate_prediction_mapping(cases, current_predictions)
    base_p_map = validate_prediction_mapping(cases, base_preds_raw)

    gains: list[str] = []
    regressions: list[str] = []
    unchanged_correct: list[str] = []
    unchanged_incorrect: list[str] = []

    for case in cases:
        cid = case["id"] if isinstance(case, dict) else case.id
        exp = case["expected"] if isinstance(case, dict) else case.expected

        curr_p = curr_p_map[cid]
        base_p = base_p_map[cid]

        curr_ok = all(curr_p.get(f) == exp.get(f) for f in EVALUATION_FIELDS)
        base_ok = all(base_p.get(f) == exp.get(f) for f in EVALUATION_FIELDS)

        if not base_ok and curr_ok:
            gains.append(cid)
        elif base_ok and not curr_ok:
            regressions.append(cid)
        elif base_ok and curr_ok:
            unchanged_correct.append(cid)
        else:
            unchanged_incorrect.append(cid)

    return {
        "gains": gains,
        "regressions": regressions,
        "unchanged_correct": unchanged_correct,
        "unchanged_incorrect": unchanged_incorrect,
        "summary": {
            "gains_count": len(gains),
            "regressions_count": len(regressions),
            "net_gain": len(gains) - len(regressions),
            "unchanged_correct_count": len(unchanged_correct),
            "unchanged_incorrect_count": len(unchanged_incorrect),
        },
    }


def run_evaluation(argv: list[str] | None = None) -> int:
    """CLI driver for evaluate command."""
    parser = argparse.ArgumentParser(
        description="Address segmentation evaluation: score predictions against cases."
    )
    parser.add_argument(
        "--dictionary",
        required=True,
        help="Path to JSON dictionary file containing streets, wards, provinces lists.",
    )
    parser.add_argument(
        "--cases",
        required=True,
        help="Path to JSONL cases file.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path to output JSON evaluation report.",
    )
    parser.add_argument(
        "--baseline",
        required=False,
        default=None,
        help="Optional path to baseline evaluation report for gains/regressions comparison.",
    )
    parser.add_argument("--config", help="Optional MatcherConfig JSON file")
    args = parser.parse_args(argv)

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
        matcher = AddressMatcher(
            streets=dict_data["streets"],
            wards=dict_data["wards"],
            provinces=dict_data["provinces"],
            config=load_config(args.config),
        )
    except (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError, OSError) as exc:
        sys.stderr.write(f"Evaluation setup error: {exc}\n")
        return 1

    current_dict_hash = sha256_file(dict_path)
    current_cases_hash = sha256_file(cases_path)

    # Run predictions
    predictions: dict[str, dict[str, str | None]] = {}
    traces = {}
    for case in cases:
        predictions[case.id], traces[case.id] = matcher.identify_with_trace(case.address)

    metrics = score_cases(cases, predictions)
    metrics["retrieval"] = score_retrieval(cases, predictions, traces)

    report: dict[str, Any] = {
        "metadata": {
            "dictionary_path": str(dict_path),
            "dictionary_hash": current_dict_hash,
            "cases_path": str(cases_path),
            "cases_hash": current_cases_hash,
            "source_hash": get_source_hash(),
            "platform": get_platform_metadata(),
            "config": get_matcher_config(matcher),
            "total_cases": len(cases),
        },
        "metrics": metrics,
        "traces": traces,
        "predictions": predictions,
        "per_case_failures": metrics["per_case_failures"],
    }

    if args.baseline:
        baseline_path = Path(args.baseline)
        if not baseline_path.is_absolute():
            baseline_path = Path.cwd() / baseline_path

        try:
            with open(baseline_path, "r", encoding="utf-8") as f:
                baseline_data = json.load(f)
            comparison = compare_baseline(
                cases=cases,
                current_predictions=predictions,
                baseline_report=baseline_data,
                current_dict_hash=current_dict_hash,
                current_cases_hash=current_cases_hash,
            )
            report["baseline_comparison"] = comparison
        except (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError, OSError) as exc:
            sys.stderr.write(f"Baseline comparison error: {exc}\n")
            return 1

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
            f.write("\n")
    except OSError as exc:
        sys.stderr.write(f"Output write error: {exc}\n")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(run_evaluation())
