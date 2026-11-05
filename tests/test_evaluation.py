"""Unit and integration tests for Stage B evaluation harness, datasets, scorer, and benchmark."""

import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import unittest

from address_segmentation.io import load_dictionary
from address_segmentation.normalize import fold_accents
from benchmarks.generate import (
    analyze_dictionary_pools,
    generate_benchmark_dataset,
    generate_synthetic_cases,
)
from tools.benchmark import _run_worker, compute_percentile, compute_stats, measure_cli
from tools.datasets import (
    EvaluationCase,
    canonical_json_hash,
    get_platform_metadata,
    get_source_hash,
    load_cases,
    sha256_bytes,
    sha256_file,
    sha256_text,
)
from tools.evaluate import compare_baseline, score_cases

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DICT_PATH = Path(__file__).resolve().parent / "fixtures" / "dictionary.json"
FIXTURE_CASES_PATH = Path(__file__).resolve().parent / "fixtures" / "cases.jsonl"


def get_cli_env() -> dict[str, str]:
    """Return environment dictionary with REPO_ROOT prepended to PYTHONPATH."""
    env = os.environ.copy()
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{REPO_ROOT}{os.pathsep}{existing_pp}" if existing_pp else str(REPO_ROOT)
    )
    return env


class TestStrictCaseLoadingAndSchema(unittest.TestCase):
    """Test JSONL schema validation, dictionary membership, duplicate IDs, and blank line rejection."""

    @classmethod
    def setUpClass(cls):
        cls.fixture_dict = load_dictionary(str(FIXTURE_DICT_PATH))

    def test_load_handwritten_fixture_cases(self):
        cases = load_cases(FIXTURE_CASES_PATH, self.fixture_dict)
        # Requirement: at least 12 handwritten cases
        self.assertGreaterEqual(len(cases), 12)

        # Check required categories present in fixture
        all_tags = {tag for c in cases for tag in c.tags}
        self.assertIn("exact", all_tags)
        self.assertIn("nfd", all_tags)
        self.assertIn("empty", all_tags)
        self.assertIn("numeric", all_tags)
        self.assertIn("collision", all_tags)
        self.assertIn("unsupported", all_tags)

        # Check case objects and indexing
        for c in cases:
            self.assertIsInstance(c, EvaluationCase)
            self.assertTrue(c.id)
            self.assertTrue(c.group_id)
            self.assertIn(c.expectation_kind, {"recoverable", "unsupported", "ambiguous"})
            # Attribute and dictionary-like access
            self.assertEqual(c["id"], c.id)
            self.assertEqual(c.get("address"), c.address)

    def test_reject_duplicate_case_id(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            f.write(
                '{"id": "case-01", "address": "Lê Lợi", "expected": {"street": "Lê Lợi", "ward": null, "province": null}, "tags": ["t"], "group_id": "g1", "expectation_kind": "recoverable"}\n'
            )
            f.write(
                '{"id": "case-01", "address": "Phường 1", "expected": {"street": null, "ward": "Phường 1", "province": null}, "tags": ["t"], "group_id": "g1", "expectation_kind": "recoverable"}\n'
            )
            tmp_path = Path(f.name)

        try:
            with self.assertRaises(ValueError) as ctx:
                load_cases(tmp_path, self.fixture_dict)
            self.assertIn("duplicate case id", str(ctx.exception))
        finally:
            tmp_path.unlink()

    def test_reject_blank_or_whitespace_lines(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            f.write(
                '{"id": "case-01", "address": "Lê Lợi", "expected": {"street": "Lê Lợi", "ward": null, "province": null}, "tags": ["t"], "group_id": "g1", "expectation_kind": "recoverable"}\n'
            )
            f.write("\n")
            tmp_path = Path(f.name)

        try:
            with self.assertRaises(ValueError) as ctx:
                load_cases(tmp_path, self.fixture_dict)
            self.assertIn("blank records are rejected", str(ctx.exception))
        finally:
            tmp_path.unlink()

    def test_reject_malformed_json_line(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            f.write("{NOT_VALID_JSON}\n")
            tmp_path = Path(f.name)

        try:
            with self.assertRaises(ValueError) as ctx:
                load_cases(tmp_path, self.fixture_dict)
            self.assertIn("malformed JSON", str(ctx.exception))
        finally:
            tmp_path.unlink()

    def test_reject_missing_case_keys(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            # Missing 'expectation_kind'
            f.write(
                '{"id": "case-01", "address": "Lê Lợi", "expected": {"street": "Lê Lợi", "ward": null, "province": null}, "tags": ["t"], "group_id": "g1"}\n'
            )
            tmp_path = Path(f.name)

        try:
            with self.assertRaises(ValueError) as ctx:
                load_cases(tmp_path, self.fixture_dict)
            self.assertIn("case keys must be exactly", str(ctx.exception))
        finally:
            tmp_path.unlink()

    def test_reject_extra_case_keys(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            # Extra key 'unknown_key'
            f.write(
                '{"id": "case-01", "address": "Lê Lợi", "expected": {"street": "Lê Lợi", "ward": null, "province": null}, "tags": ["t"], "group_id": "g1", "expectation_kind": "recoverable", "unknown_key": 123}\n'
            )
            tmp_path = Path(f.name)

        try:
            with self.assertRaises(ValueError) as ctx:
                load_cases(tmp_path, self.fixture_dict)
            self.assertIn("case keys must be exactly", str(ctx.exception))
        finally:
            tmp_path.unlink()

    def test_reject_non_dictionary_expected_label(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            # Street "Đường Không Tồn Tại" is not in fixture dictionary
            f.write(
                '{"id": "case-01", "address": "Đường Không Tồn Tại", "expected": {"street": "Đường Không Tồn Tại", "ward": null, "province": null}, "tags": ["t"], "group_id": "g1", "expectation_kind": "recoverable"}\n'
            )
            tmp_path = Path(f.name)

        try:
            with self.assertRaises(ValueError) as ctx:
                load_cases(tmp_path, self.fixture_dict)
            self.assertIn("does not exist in supplied dictionary", str(ctx.exception))
        finally:
            tmp_path.unlink()

    def test_reject_invalid_expectation_kind(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            f.write(
                '{"id": "case-01", "address": "Lê Lợi", "expected": {"street": "Lê Lợi", "ward": null, "province": null}, "tags": ["t"], "group_id": "g1", "expectation_kind": "invalid_kind"}\n'
            )
            tmp_path = Path(f.name)

        try:
            with self.assertRaises(ValueError) as ctx:
                load_cases(tmp_path, self.fixture_dict)
            self.assertIn("'expectation_kind' must be one of", str(ctx.exception))
        finally:
            tmp_path.unlink()

    def test_reject_blank_id_or_group_id(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            f.write(
                '{"id": "   ", "address": "Lê Lợi", "expected": {"street": "Lê Lợi", "ward": null, "province": null}, "tags": ["t"], "group_id": "g1", "expectation_kind": "recoverable"}\n'
            )
            tmp_path = Path(f.name)

        try:
            with self.assertRaises(ValueError) as ctx:
                load_cases(tmp_path, self.fixture_dict)
            self.assertIn("'id' must be a nonblank string", str(ctx.exception))
        finally:
            tmp_path.unlink()


class TestScorerHandCalculatedRatesAndZeroDenominators(unittest.TestCase):
    """Test pure scoring function against hand-calculated rates and edge cases."""

    def test_zero_denominator_rates_return_none(self):
        # Empty cases list: denominator is 0 for all rates
        metrics = score_cases(cases=[], predictions={})
        self.assertEqual(metrics["complete_accuracy"]["numerator"], 0)
        self.assertEqual(metrics["complete_accuracy"]["denominator"], 0)
        self.assertIsNone(metrics["complete_accuracy"]["rate"])

        self.assertIsNone(metrics["per_field_accuracy"]["overall"]["rate"])
        self.assertIsNone(metrics["false_non_null"]["overall"]["rate"])
        self.assertIsNone(metrics["wrong_label"]["overall"]["rate"])
        self.assertIsNone(metrics["missed_nonnull"]["overall"]["rate"])
        self.assertIsNone(metrics["missed_recoverable"]["overall"]["rate"])
        self.assertIsNone(metrics["ambiguity_abstention"]["case_level"]["rate"])
        self.assertIsNone(metrics["ambiguity_abstention"]["field_level"]["rate"])

    def test_hand_calculated_rates_verification(self):
        c1 = EvaluationCase(
            id="c1",
            address="addr 1",
            expected={"street": "Lê Lợi", "ward": "Phường 1", "province": "Thành phố Hồ Chí Minh"},
            tags=("t1",),
            group_id="g1",
            expectation_kind="recoverable",
        )
        c2 = EvaluationCase(
            id="c2",
            address="addr 2",
            expected={"street": "3/2", "ward": "Phường 2", "province": None},
            tags=("t2",),
            group_id="g1",
            expectation_kind="recoverable",
        )
        c3 = EvaluationCase(
            id="c3",
            address="addr 3",
            expected={"street": None, "ward": "Phường Bến Thành", "province": "Thành phố Hà Nội"},
            tags=("t3",),
            group_id="g2",
            expectation_kind="ambiguous",
        )
        cases = [c1, c2, c3]

        predictions = {
            "c1": {"street": "Lê Lợi", "ward": "Phường 1", "province": "Thành phố Hồ Chí Minh"},
            "c2": {"street": "3/2", "ward": "Phường 1", "province": "Hòa Bình"},
            "c3": {"street": None, "ward": None, "province": "Thành phố Hà Nội"},
        }

        metrics = score_cases(cases, predictions)

        # Complete accuracy: 1 out of 3 cases (c1 only)
        self.assertEqual(metrics["complete_accuracy"]["numerator"], 1)
        self.assertEqual(metrics["complete_accuracy"]["denominator"], 3)
        self.assertAlmostEqual(metrics["complete_accuracy"]["rate"], 1 / 3)

        # Per field accuracy:
        self.assertEqual(metrics["per_field_accuracy"]["by_field"]["street"]["numerator"], 3)
        self.assertEqual(metrics["per_field_accuracy"]["by_field"]["street"]["denominator"], 3)
        self.assertEqual(metrics["per_field_accuracy"]["by_field"]["street"]["rate"], 1.0)

        self.assertEqual(metrics["per_field_accuracy"]["by_field"]["ward"]["numerator"], 1)
        self.assertEqual(metrics["per_field_accuracy"]["by_field"]["ward"]["denominator"], 3)
        self.assertAlmostEqual(metrics["per_field_accuracy"]["by_field"]["ward"]["rate"], 1 / 3)

        self.assertEqual(metrics["per_field_accuracy"]["by_field"]["province"]["numerator"], 2)
        self.assertEqual(metrics["per_field_accuracy"]["by_field"]["province"]["denominator"], 3)
        self.assertAlmostEqual(metrics["per_field_accuracy"]["by_field"]["province"]["rate"], 2 / 3)

        self.assertEqual(metrics["per_field_accuracy"]["overall"]["numerator"], 6)
        self.assertEqual(metrics["per_field_accuracy"]["overall"]["denominator"], 9)
        self.assertAlmostEqual(metrics["per_field_accuracy"]["overall"]["rate"], 6 / 9)

        # False non-null
        self.assertEqual(metrics["false_non_null"]["overall"]["numerator"], 1)
        self.assertEqual(metrics["false_non_null"]["overall"]["denominator"], 2)
        self.assertEqual(metrics["false_non_null"]["overall"]["rate"], 0.5)

        # Wrong label
        self.assertEqual(metrics["wrong_label"]["overall"]["numerator"], 1)
        self.assertEqual(metrics["wrong_label"]["overall"]["denominator"], 7)
        self.assertAlmostEqual(metrics["wrong_label"]["overall"]["rate"], 1 / 7)

        # Missed non-null
        self.assertEqual(metrics["missed_nonnull"]["overall"]["numerator"], 1)
        self.assertEqual(metrics["missed_nonnull"]["overall"]["denominator"], 7)
        self.assertAlmostEqual(metrics["missed_nonnull"]["overall"]["rate"], 1 / 7)

        # Missed recoverable
        self.assertEqual(metrics["missed_recoverable"]["overall"]["numerator"], 0)
        self.assertEqual(metrics["missed_recoverable"]["overall"]["denominator"], 5)
        self.assertEqual(metrics["missed_recoverable"]["overall"]["rate"], 0.0)

        # Ambiguity abstention
        self.assertEqual(metrics["ambiguity_abstention"]["case_level"]["numerator"], 1)
        self.assertEqual(metrics["ambiguity_abstention"]["case_level"]["denominator"], 1)
        self.assertEqual(metrics["ambiguity_abstention"]["case_level"]["rate"], 1.0)

        self.assertEqual(metrics["category_counts"]["total_cases"], 3)
        self.assertEqual(metrics["category_counts"]["by_expectation_kind"], {"recoverable": 2, "ambiguous": 1})

        failures = metrics["per_case_failures"]
        self.assertEqual(len(failures), 2)
        failed_ids = {f["id"] for f in failures}
        self.assertEqual(failed_ids, {"c2", "c3"})

        self.assertEqual(metrics["retrieval"]["status"], "unavailable")
        self.assertIsNone(metrics["retrieval"]["recall_before_verification"])

    def test_incomplete_or_invalid_prediction_maps_fail(self):
        c1 = EvaluationCase(
            id="c1",
            address="addr 1",
            expected={"street": "Lê Lợi", "ward": None, "province": None},
            tags=(),
            group_id="g1",
            expectation_kind="recoverable",
        )
        c2 = EvaluationCase(
            id="c2",
            address="addr 2",
            expected={"street": "3/2", "ward": None, "province": None},
            tags=(),
            group_id="g1",
            expectation_kind="recoverable",
        )
        valid_pred = {"street": "Lê Lợi", "ward": None, "province": None}

        # Missing prediction for c2
        with self.assertRaises(ValueError) as ctx:
            score_cases([c1, c2], {"c1": valid_pred})
        self.assertIn("missing", str(ctx.exception).lower())

        # Extra prediction for c3
        with self.assertRaises(ValueError) as ctx:
            score_cases([c1], {"c1": valid_pred, "c3": valid_pred})
        self.assertIn("extra", str(ctx.exception).lower())

        # Sequence length mismatch
        with self.assertRaises(ValueError) as ctx:
            score_cases([c1, c2], [valid_pred])
        self.assertIn("length", str(ctx.exception).lower())

        # Duplicate ID in ID-tagged prediction list
        with self.assertRaises(ValueError) as ctx:
            score_cases([c1, c2], [{"id": "c1", **valid_pred}, {"id": "c1", **valid_pred}])
        self.assertIn("duplicate", str(ctx.exception).lower())

        # Missing field key in prediction
        with self.assertRaises(ValueError) as ctx:
            score_cases([c1], {"c1": {"street": "Lê Lợi", "ward": None}})
        self.assertIn("field keys must be exactly", str(ctx.exception))

        # Extra field key in prediction
        with self.assertRaises(ValueError) as ctx:
            score_cases([c1], {"c1": {"street": "Lê Lợi", "ward": None, "province": None, "extra": "x"}})
        self.assertIn("field keys must be exactly", str(ctx.exception))

        # Non-string / non-null value
        with self.assertRaises(TypeError) as ctx:
            score_cases([c1], {"c1": {"street": 12345, "ward": None, "province": None}})
        self.assertIn("must be str or None", str(ctx.exception))

        # Baseline comparison with incomplete prediction map
        with self.assertRaises(ValueError):
            compare_baseline(
                cases=[c1, c2],
                current_predictions={"c1": valid_pred},
                baseline_report={
                    "metadata": {"dictionary_hash": "h1", "cases_hash": "h2"},
                    "predictions": {"c1": valid_pred, "c2": valid_pred},
                },
                current_dict_hash="h1",
                current_cases_hash="h2",
            )


class TestBaselineComparison(unittest.TestCase):
    """Test baseline gains, regressions, unchanged classifications, and dataset hash enforcement."""

    def test_baseline_gains_and_regressions(self):
        cases = [
            EvaluationCase(
                id="c1",
                address="addr 1",
                expected={"street": "Lê Lợi", "ward": None, "province": None},
                tags=(),
                group_id="g1",
                expectation_kind="recoverable",
            ),
            EvaluationCase(
                id="c2",
                address="addr 2",
                expected={"street": "3/2", "ward": None, "province": None},
                tags=(),
                group_id="g1",
                expectation_kind="recoverable",
            ),
            EvaluationCase(
                id="c3",
                address="addr 3",
                expected={"street": None, "ward": "Phường 1", "province": None},
                tags=(),
                group_id="g1",
                expectation_kind="recoverable",
            ),
        ]

        # Baseline predictions: c1 correct, c2 wrong, c3 wrong
        baseline_report = {
            "metadata": {
                "dictionary_hash": "dict_hash_123",
                "cases_hash": "cases_hash_456",
            },
            "predictions": {
                "c1": {"street": "Lê Lợi", "ward": None, "province": None},
                "c2": {"street": "WRONG", "ward": None, "province": None},
                "c3": {"street": "WRONG", "ward": None, "province": None},
            },
        }

        # Current predictions: c1 regressed, c2 gained, c3 remains wrong
        current_predictions = {
            "c1": {"street": "WRONG", "ward": None, "province": None},
            "c2": {"street": "3/2", "ward": None, "province": None},
            "c3": {"street": "STILL_WRONG", "ward": None, "province": None},
        }

        comparison = compare_baseline(
            cases=cases,
            current_predictions=current_predictions,
            baseline_report=baseline_report,
            current_dict_hash="dict_hash_123",
            current_cases_hash="cases_hash_456",
        )

        self.assertEqual(comparison["gains"], ["c2"])
        self.assertEqual(comparison["regressions"], ["c1"])
        self.assertEqual(comparison["unchanged_correct"], [])
        self.assertEqual(comparison["unchanged_incorrect"], ["c3"])
        self.assertEqual(comparison["summary"]["gains_count"], 1)
        self.assertEqual(comparison["summary"]["regressions_count"], 1)
        self.assertEqual(comparison["summary"]["net_gain"], 0)

    def test_baseline_dataset_hash_mismatch_rejected(self):
        baseline_report = {
            "metadata": {
                "dictionary_hash": "dict_hash_old",
                "cases_hash": "cases_hash_old",
            },
            "predictions": {},
        }
        with self.assertRaises(ValueError) as ctx:
            compare_baseline(
                cases=[],
                current_predictions={},
                baseline_report=baseline_report,
                current_dict_hash="dict_hash_new",
                current_cases_hash="cases_hash_new",
            )
        self.assertIn("Baseline dataset hash mismatch", str(ctx.exception))


class TestPercentileMath(unittest.TestCase):
    """Test percentile calculations and summary statistics."""

    def test_percentile_known_sequence(self):
        values = list(range(101))
        self.assertEqual(compute_percentile(values, 50.0), 50.0)
        self.assertEqual(compute_percentile(values, 95.0), 95.0)
        self.assertEqual(compute_percentile(values, 99.0), 99.0)

    def test_single_value_statistics(self):
        stats = compute_stats([5_000_000])  # 5 ms in ns
        self.assertEqual(stats["count"], 1)
        self.assertEqual(stats["mean_ms"], 5.0)
        self.assertEqual(stats["min_ms"], 5.0)
        self.assertEqual(stats["max_ms"], 5.0)
        self.assertEqual(stats["p50_ms"], 5.0)
        self.assertEqual(stats["p95_ms"], 5.0)
        self.assertEqual(stats["p99_ms"], 5.0)

    def test_empty_statistics(self):
        stats = compute_stats([])
        self.assertEqual(stats["count"], 0)
        self.assertEqual(stats["mean_ms"], 0.0)
        self.assertEqual(stats["p50_ms"], 0.0)


class TestDeterministicGenerator(unittest.TestCase):
    """Test deterministic benchmark generator: byte-for-byte reproducibility and exact counts."""

    def test_repeat_generator_identical_bytes(self):
        with tempfile.TemporaryDirectory(prefix="bench_gen_1_") as dir1, tempfile.TemporaryDirectory(
            prefix="bench_gen_2_"
        ) as dir2:
            path1 = Path(dir1)
            path2 = Path(dir2)

            m1 = generate_benchmark_dataset(
                output_dir=path1,
                seed=42,
                streets_count=50,
                wards_count=15,
                provinces_count=5,
                cases_count=20,
            )
            m2 = generate_benchmark_dataset(
                output_dir=path2,
                seed=42,
                streets_count=50,
                wards_count=15,
                provinces_count=5,
                cases_count=20,
            )

            # Manifest metadata agreement
            self.assertEqual(m1, m2)

            # Byte-for-byte identical output files
            for filename in ("dictionary.json", "cases.jsonl", "manifest.json"):
                b1 = (path1 / filename).read_bytes()
                b2 = (path2 / filename).read_bytes()
                self.assertEqual(
                    b1,
                    b2,
                    f"File {filename} produced different bytes across repeated runs with seed 42",
                )

    def test_exact_requested_counts_and_strict_validation(self):
        with tempfile.TemporaryDirectory(prefix="bench_counts_") as tmp_dir:
            out_dir = Path(tmp_dir)
            generate_benchmark_dataset(
                output_dir=out_dir,
                seed=123,
                streets_count=45,
                wards_count=12,
                provinces_count=6,
                cases_count=25,
            )

            dict_data = load_dictionary(str(out_dir / "dictionary.json"))
            self.assertEqual(len(dict_data["streets"]), 45)
            self.assertEqual(len(dict_data["wards"]), 12)
            self.assertEqual(len(dict_data["provinces"]), 6)

            cases = load_cases(out_dir / "cases.jsonl", dict_data)
            self.assertEqual(len(cases), 25)

    def test_generator_rejects_non_positive_counts(self):
        with tempfile.TemporaryDirectory(prefix="bench_err_") as tmp_dir:
            out_dir = Path(tmp_dir)
            with self.assertRaises(ValueError):
                generate_benchmark_dataset(output_dir=out_dir, streets_count=0)
            with self.assertRaises(ValueError):
                generate_benchmark_dataset(output_dir=out_dir, cases_count=-5)

    def test_folded_collision_generation_does_not_blindly_preserve_source_label(self):
        custom_dict = {
            "streets": ["Hòa Bình", "Hóa Bình", "Lê Lợi"],
            "wards": ["Phường 1"],
            "provinces": ["Thành phố Hồ Chí Minh"],
        }
        safe_pools, proven_cross, proven_folded = analyze_dictionary_pools(custom_dict)
        self.assertNotIn("Hòa Bình", safe_pools["street"])
        self.assertNotIn("Hóa Bình", safe_pools["street"])
        self.assertIn("Lê Lợi", safe_pools["street"])
        self.assertEqual(len(proven_folded), 1)

        rng = random.Random(42)
        cases = generate_synthetic_cases(cases_count=30, dictionaries=custom_dict, rng=rng)
        for case in cases:
            exp = case["expected"]
            if "hoa binh" in fold_accents(case["address"]):
                self.assertIsNone(
                    exp["street"],
                    f"Case {case['id']} blindly preserved label for folded collision",
                )

    def test_small_count_generation_never_invents_absent_collision_labels(self):
        with tempfile.TemporaryDirectory(prefix="small_counts_") as tmp_dir:
            out_dir = Path(tmp_dir)
            generate_benchmark_dataset(
                output_dir=out_dir,
                seed=42,
                streets_count=1,
                wards_count=1,
                provinces_count=1,
                cases_count=15,
            )
            dict_data = load_dictionary(str(out_dir / "dictionary.json"))
            self.assertEqual(len(dict_data["streets"]), 1)
            self.assertEqual(len(dict_data["wards"]), 1)
            self.assertEqual(len(dict_data["provinces"]), 1)

            street_label = dict_data["streets"][0]
            ward_label = dict_data["wards"][0]
            prov_label = dict_data["provinces"][0]

            cases = load_cases(out_dir / "cases.jsonl", dict_data)
            self.assertEqual(len(cases), 15)

            for c in cases:
                exp = c.expected
                if exp["street"] is not None:
                    self.assertEqual(exp["street"], street_label)
                if exp["ward"] is not None:
                    self.assertEqual(exp["ward"], ward_label)
                if exp["province"] is not None:
                    self.assertEqual(exp["province"], prov_label)
                if "Hòa Bình" not in (street_label, prov_label):
                    self.assertNotEqual(exp["street"], "Hòa Bình")
                    self.assertNotEqual(exp["province"], "Hòa Bình")


class TestColdAndWarmExecution(unittest.TestCase):
    """Test cold request isolation before warmup and timer verification."""

    def test_cli_timer_includes_startup_and_stdout_but_separates_exit(self):
        command = [sys.executable, "-c", (
            "import json, sys, time; "
            "time.sleep(0.06); "
            "address = json.loads(sys.stdin.readline()); "
            "print(json.dumps(dict(street=address, ward=None, province=None)), flush=True); "
            "time.sleep(0.15)"
        )]
        elapsed, exit_elapsed, predictions = measure_cli(command, ["Lê Lợi"], get_cli_env(), 5)
        self.assertGreaterEqual(elapsed, 60_000_000)
        self.assertGreaterEqual(exit_elapsed - elapsed, 100_000_000)
        self.assertEqual(predictions, [{"street": "Lê Lợi", "ward": None, "province": None}])

    def test_cli_rejects_missing_extra_or_malformed_console_results(self):
        for output in ("", "{}", "debug log", '{"street": 2, "ward": null, "province": null}',
                       '{"street": null, "ward": null, "province": null}\\n{}'):
            with self.subTest(output=output), self.assertRaises(ValueError):
                measure_cli([sys.executable, "-c", f"print({output!r})"],
                            ["address"], get_cli_env(), 5)

    def test_cli_failure_and_timeout_cannot_be_reported_as_fast_success(self):
        with self.assertRaises(subprocess.CalledProcessError):
            measure_cli([sys.executable, "-c", "raise SystemExit(1)"],
                        ["address"], get_cli_env(), 5)
        with self.assertRaises(subprocess.TimeoutExpired):
            measure_cli([sys.executable, "-c", "import time; time.sleep(5)"],
                        ["address"], get_cli_env(), 0.05)

    def test_cli_drains_large_input_stdout_and_stderr_without_deadlock(self):
        command = [sys.executable, "-c", (
            "import json, sys\n"
            "for line in sys.stdin:\n"
            "    sys.stderr.write('diagnostic' * 100)\n"
            "    print(json.dumps(dict(street=json.loads(line), ward=None, province=None)), flush=True)\n"
        )]
        addresses = ["Lê Lợi " * 300 for _ in range(100)]
        _, _, predictions = measure_cli(command, addresses, get_cli_env(), 5)
        self.assertEqual([prediction["street"] for prediction in predictions], addresses)

    def test_cold_request_occurs_before_warmup(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as out_f:
            out_path = out_f.name

        try:
            _run_worker(
                dict_path=str(FIXTURE_DICT_PATH),
                cases_path=str(FIXTURE_CASES_PATH),
                warmup=4,
                repetitions=2,
                seed=42,
                worker_out_path=out_path,
            )
            with open(out_path, "r", encoding="utf-8") as f:
                w_res = json.load(f)

            self.assertIn("first_case_id", w_res)
            self.assertIn("first_request_ns", w_res)
            self.assertEqual(w_res["warmup_count"], 4)
            self.assertEqual(w_res["repetition_count"], 2)

            fixture_dict = load_dictionary(str(FIXTURE_DICT_PATH))
            cases = load_cases(FIXTURE_CASES_PATH, fixture_dict)
            self.assertEqual(w_res["first_case_id"], cases[0].id)
            self.assertEqual(w_res["warmed_request_count"], len(cases) * 2)
        finally:
            Path(out_path).unlink()

    def test_fake_matcher_cold_differs_from_warmed(self):
        class TimedMatcher:
            def __init__(self):
                self.count = 0

            def identify(self, address: str):
                self.count += 1
                if self.count == 1:
                    return {"street": "Cold", "ward": None, "province": None}, 45_000_000
                return {"street": "Warm", "ward": None, "province": None}, 1_500_000

        m = TimedMatcher()
        res1, t1 = m.identify("addr 1")
        res2, t2 = m.identify("addr 2")
        self.assertNotEqual(t1, t2)
        self.assertGreater(t1, t2)
        self.assertEqual(res1["street"], "Cold")
        self.assertEqual(res2["street"], "Warm")


class TestMetadataAndHashingHelpers(unittest.TestCase):
    """Test SHA-256 helpers, source hashing, and platform metadata."""

    def test_canonical_sha256_helpers(self):
        data = b"Antigravity Stage B"
        expected_hex = "acfbef3218f6ad623bb16911660d9efb9546552f013abcd049ff56f5a7ffc99f"
        self.assertEqual(sha256_bytes(data), expected_hex)
        self.assertEqual(sha256_text("Antigravity Stage B"), expected_hex)

        with tempfile.NamedTemporaryFile("wb", delete=False) as f:
            f.write(data)
            tmp_p = Path(f.name)
        try:
            self.assertEqual(sha256_file(tmp_p), expected_hex)
        finally:
            tmp_p.unlink()

    def test_canonical_json_hash(self):
        obj1 = {"b": 2, "a": 1}
        obj2 = {"a": 1, "b": 2}
        self.assertEqual(canonical_json_hash(obj1), canonical_json_hash(obj2))

    def test_get_source_hash_deterministic(self):
        h1 = get_source_hash(REPO_ROOT)
        h2 = get_source_hash(REPO_ROOT)
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 64)

    def test_get_platform_metadata(self):
        meta = get_platform_metadata()
        self.assertIn("python_version", meta)
        self.assertIn("platform", meta)
        self.assertIn("system", meta)


class TestCLIIntegration(unittest.TestCase):
    """Test CLI commands for evaluate and benchmark."""

    def test_evaluate_cli_run(self):
        with tempfile.TemporaryDirectory(prefix="eval_cli_") as tmp_dir:
            out_report = Path(tmp_dir) / "baseline.json"
            cmd = [
                sys.executable,
                "-m",
                "tools.evaluate",
                "--dictionary",
                str(FIXTURE_DICT_PATH),
                "--cases",
                str(FIXTURE_CASES_PATH),
                "--output",
                str(out_report),
            ]
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                env=get_cli_env(),
                check=False,
            )
            self.assertEqual(proc.returncode, 0, f"evaluate stderr: {proc.stderr}")
            self.assertEqual(proc.stdout, "")  # No stdout debug!

            self.assertTrue(out_report.exists())
            with open(out_report, "r", encoding="utf-8") as f:
                report = json.load(f)

            self.assertIn("metadata", report)
            self.assertIn("metrics", report)
            self.assertIn("predictions", report)
            self.assertIn("per_case_failures", report)

    def test_benchmark_cli_argument_validation(self):
        cmd_invalid_proc = [
            sys.executable,
            "-m",
            "tools.benchmark",
            "--dictionary",
            str(FIXTURE_DICT_PATH),
            "--cases",
            str(FIXTURE_CASES_PATH),
            "--output",
            "/tmp/timing.json",
            "--processes",
            "0",
        ]
        proc = subprocess.run(
            cmd_invalid_proc,
            capture_output=True,
            text=True,
            env=get_cli_env(),
            check=False,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("--processes must be a positive integer", proc.stderr)

    def test_benchmark_cli_smoke_run(self):
        with tempfile.TemporaryDirectory(prefix="bench_smoke_") as tmp_dir:
            out_timing = Path(tmp_dir) / "timing.json"
            cmd = [
                sys.executable,
                "-m",
                "tools.benchmark",
                "--dictionary",
                str(FIXTURE_DICT_PATH),
                "--cases",
                str(FIXTURE_CASES_PATH),
                "--output",
                str(out_timing),
                "--processes",
                "1",
                "--warmup",
                "2",
                "--repetitions",
                "1",
                "--seed",
                "42",
            ]
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                env=get_cli_env(),
                check=False,
            )
            self.assertEqual(proc.returncode, 0, f"benchmark stderr: {proc.stderr}")

            self.assertTrue(out_timing.exists())
            with open(out_timing, "r", encoding="utf-8") as f:
                report = json.load(f)

            self.assertIn("phases", report)
            self.assertIn("memory", report)
            self.assertIn("thresholds", report)
            self.assertIn("gc_policy", report)
            self.assertIn("raw_request_timings", report)
            self.assertIn("fresh_process_import", report["phases"])
            self.assertIn("interpreter_and_import_total", report["phases"])
            self.assertIn("full_jsonl_adapter", report["phases"])
            self.assertEqual(report["thresholds"]["scope"], "cold_process_request")
            cold = report["phases"]["cold_process_request"]
            self.assertEqual(cold["count"], report["metadata"]["total_cases"])
            self.assertTrue(report["cold_coverage"]["full_dataset"])
            self.assertEqual(report["cold_coverage"]["warmup"], 0)
            self.assertEqual(len(report["raw_cold_process_timings"]), cold["count"])
            self.assertEqual(report["thresholds"]["observed_overall_max_ms"], cold["max_ms"])
            self.assertEqual(report["thresholds"]["mean_target_met"], cold["mean_ms"] <= 10)
            self.assertEqual(report["thresholds"]["max_target_met"], cold["max_ms"] <= 100)
            self.assertAlmostEqual(report["batch_process"]["amortized_per_case"]["mean_ms"],
                                   report["phases"]["full_jsonl_adapter"]["mean_ms"] / cold["count"])
            self.assertIn("Cold process per address:", proc.stdout)

    def test_benchmark_sample_retains_coverage_and_independent_rounds(self):
        with tempfile.TemporaryDirectory(prefix="bench_sample_") as tmp_dir:
            out_timing = Path(tmp_dir) / "timing.json"
            proc = subprocess.run(
                [sys.executable, "-m", "tools.benchmark", "--dictionary", str(FIXTURE_DICT_PATH),
                 "--cases", str(FIXTURE_CASES_PATH), "--output", str(out_timing),
                 "--processes", "2", "--cold-cases", "1", "--warmup", "0", "--repetitions", "1"],
                capture_output=True, text=True, env=get_cli_env(), check=False)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            report = json.loads(out_timing.read_text())
            coverage = report["cold_coverage"]
            self.assertFalse(coverage["full_dataset"])
            self.assertEqual(coverage["selection"], "seeded_uniform_sample_without_replacement")
            cases = load_cases(FIXTURE_CASES_PATH, load_dictionary(str(FIXTURE_DICT_PATH)))
            self.assertEqual(coverage["selected_case_ids"], [random.Random(42).sample(cases, 1)[0].id])
            records = report["raw_cold_process_timings"]
            self.assertEqual(len(records), 2)
            self.assertEqual([record["round"] for record in records], [0, 1])
            self.assertEqual(records[0]["case_id"], records[1]["case_id"])
            self.assertEqual(report["phases"]["full_jsonl_adapter"]["count"], 2)


if __name__ == "__main__":
    unittest.main()
