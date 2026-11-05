"""Comprehensive tests for Stage D approximate recovery, thresholded OSA, and budgets."""

import itertools
import json
from pathlib import Path
import unittest

from address_segmentation.catalog import Catalog
from address_segmentation.matcher import AddressMatcher
from address_segmentation.model import Candidate, MatcherConfig
from address_segmentation.verify import (
    osa_distance,
    osa_distance_reference,
    thresholded_osa_distance,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
DICT_PATH = FIXTURES_DIR / "recovery-dictionary.json"
CASES_PATH = FIXTURES_DIR / "recovery-cases.jsonl"


class TestVerifyOSA(unittest.TestCase):
    """Test Optimal String Alignment distance, thresholding, and boundary conditions."""

    def test_exact_matches(self):
        self.assertEqual(thresholded_osa_distance("", "", 0), 0)
        self.assertEqual(thresholded_osa_distance("abc", "abc", 1), 0)
        self.assertEqual(thresholded_osa_distance("Phường 1", "Phường 1", 2), 0)

    def test_single_operations(self):
        # Substitution
        self.assertEqual(thresholded_osa_distance("cat", "cot", 1), 1)
        # Insertion
        self.assertEqual(thresholded_osa_distance("cat", "cats", 1), 1)
        # Deletion
        self.assertEqual(thresholded_osa_distance("cats", "cat", 1), 1)
        # Adjacent transposition (swap)
        self.assertEqual(thresholded_osa_distance("dnog", "dong", 1), 1)
        self.assertEqual(thresholded_osa_distance("ab", "ba", 1), 1)
        self.assertEqual(thresholded_osa_distance("phuong", "phunog", 1), 1)

    def test_limit_boundaries_and_rejection(self):
        # Distance = 1, limit = 0 -> returns limit + 1 = 1
        self.assertEqual(thresholded_osa_distance("cat", "cot", 0), 1)
        # Distance = 2, limit = 1 -> returns limit + 1 = 2
        self.assertEqual(thresholded_osa_distance("cat", "dog", 1), 2)
        # Distance = 2, limit = 2 -> returns 2
        self.assertEqual(thresholded_osa_distance("cat", "caro", 2), 2)
        # Limit < 0
        self.assertEqual(thresholded_osa_distance("cat", "cot", -1), 0)

    def test_early_length_rejection(self):
        # Length diff 3 with limit 1 -> immediately returns 2
        self.assertEqual(thresholded_osa_distance("a", "abcd", 1), 2)
        self.assertEqual(thresholded_osa_distance("abcdef", "ab", 2), 3)

    def test_exhaustive_small_strings_vs_reference(self):
        alphabet = ["", "a", "b", "c"]
        all_strings = [""]
        for length in (1, 2, 3):
            for p in itertools.product(["a", "b", "c"], repeat=length):
                all_strings.append("".join(p))

        for s1 in all_strings:
            for s2 in all_strings:
                ref_dist = osa_distance_reference(s1, s2)
                for limit in (0, 1, 2, 3):
                    expected = ref_dist if ref_dist <= limit else limit + 1
                    actual = thresholded_osa_distance(s1, s2, limit)
                    self.assertEqual(
                        actual,
                        expected,
                        f"Mismatch for s1={s1!r}, s2={s2!r}, limit={limit}: expected {expected}, got {actual}",
                    )


class TestMatcherConfigValidation(unittest.TestCase):
    """Test configuration validation for Stage D bounds, positive integers, and flags."""

    def test_stage_d_defaults(self):
        config = MatcherConfig()
        self.assertEqual(config.max_fuzzy_spans, 128)
        self.assertEqual(config.max_candidates_per_span, 64)
        self.assertEqual(config.max_verifications, 2048)
        self.assertEqual(config.max_postings, 12000)
        self.assertEqual(config.max_edits, 2)
        self.assertTrue(config.enable_fuzzy)

    def test_reject_invalid_bounds_types_and_values(self):
        for attr in (
            "max_fuzzy_spans",
            "max_candidates_per_span",
            "max_verifications",
            "max_postings",
            "max_edits",
        ):
            with self.assertRaises(TypeError):
                MatcherConfig(**{attr: "100"})
            with self.assertRaises(TypeError):
                MatcherConfig(**{attr: True})
            with self.assertRaises(TypeError):
                MatcherConfig(**{attr: 10.5})
            with self.assertRaises(ValueError):
                MatcherConfig(**{attr: 0})
            with self.assertRaises(ValueError):
                MatcherConfig(**{attr: -5})


class TestRecoverySemantics(unittest.TestCase):
    """Test recovery operations: typos, accents, prefix supersession, and negative controls."""

    @classmethod
    def setUpClass(cls):
        with open(DICT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        cls.matcher = AddressMatcher(
            streets=data["streets"],
            wards=data["wards"],
            provinces=data["provinces"],
        )

    def test_transposition_recovery_unaccented(self):
        res = self.matcher.identify("phuong an phu dnog, Thanh pho Ho Chi Minh")
        self.assertEqual(res["ward"], "Phường An Phú Đông")
        self.assertEqual(res["province"], "Thành phố Hồ Chí Minh")

    def test_substitution_recovery_accented(self):
        res = self.matcher.identify("Đường Le Loj, Phường Bến Thành, Thành phố Hồ Chí Minh")
        self.assertEqual(res["street"], "Lê Lợi")
        self.assertEqual(res["ward"], "Phường Bến Thành")
        self.assertEqual(res["province"], "Thành phố Hồ Chí Minh")

    def test_insertion_recovery(self):
        res = self.matcher.identify("Đường Tran Huong Dao, Phường Bến Thành")
        self.assertEqual(res["street"], "Trần Hưng Đạo")
        self.assertEqual(res["ward"], "Phường Bến Thành")

    def test_deletion_recovery(self):
        res = self.matcher.identify("Đường Dien Bien Pu, Phường Bến Thành")
        self.assertEqual(res["street"], "Điện Biên Phủ")
        self.assertEqual(res["ward"], "Phường Bến Thành")

    def test_multiword_transposition_with_house_number(self):
        res = self.matcher.identify("56/33 Nam Ky Khoi Ngiha, Phường Bến Thành, TP. Hồ Chí Minh")
        self.assertEqual(res["street"], "Nam Kỳ Khởi Nghĩa")
        self.assertEqual(res["ward"], "Phường Bến Thành")

    def test_exact_prefix_with_unexplained_tail_recovers_longer_entry(self):
        # 'An Phú' matches as exact prefix, but 'Dnog' is trailing unexplained in same component
        # Plausible fuzzy 'An Phú Đông' must supersede exact prefix 'An Phú'
        res = self.matcher.identify("Đường An Phú Dnog, Phường 1, Thành phố Hồ Chí Minh")
        self.assertEqual(res["street"], "An Phú Đông")
        self.assertEqual(res["ward"], "Phường 1")
        self.assertEqual(res["province"], "Thành phố Hồ Chí Minh")

    def test_fast_path_full_exact_address(self):
        addr = "56/33 Nam Kỳ Khởi Nghĩa, Phường Bến Thành, Thành phố Hồ Chí Minh"
        res, trace = self.matcher.identify_with_trace(addr)
        self.assertEqual(
            res,
            {
                "street": "Nam Kỳ Khởi Nghĩa",
                "ward": "Phường Bến Thành",
                "province": "Thành phố Hồ Chí Minh",
            },
        )
        # Fast path bypassed costly approximate verifications and postings
        budgets = trace["budgets"]
        self.assertEqual(budgets["counts"]["verifications"], 0)
        self.assertEqual(budgets["counts"]["postings"], 0)
        self.assertFalse(budgets["exhausted"])

    def test_negative_control_numeric_name_exact_only(self):
        # Street '3/2' cannot be fuzzily repaired to '3/3'
        res = self.matcher.identify("Đường 3/3, Phường 1, Thành phố Hồ Chí Minh")
        self.assertIsNone(res["street"])
        self.assertEqual(res["ward"], "Phường 1")
        self.assertEqual(res["province"], "Thành phố Hồ Chí Minh")

    def test_negative_control_short_noise_remains_null(self):
        # Single-letter or very short noise must not match
        res = self.matcher.identify("Đường L, Phường 1, Thành phố Hồ Chí Minh")
        self.assertIsNone(res["street"])
        self.assertEqual(res["ward"], "Phường 1")

    def test_negative_control_delivery_notes_not_labeled(self):
        res = self.matcher.identify("Giao hàng nhanh buổi chiều, Phường Bến Thành, Thành phố Hồ Chí Minh")
        self.assertIsNone(res["street"])
        self.assertEqual(res["ward"], "Phường Bến Thành")
        self.assertEqual(res["province"], "Thành phố Hồ Chí Minh")

    def test_component_order_permutations_with_typo(self):
        addr1 = "Đường Le Loj, Phường Bến Thnah, Thành phố Hồ Chí Minh"
        addr2 = "Thành phố Hồ Chí Minh, Phường Bến Thnah, Đường Le Loj"
        res1 = self.matcher.identify(addr1)
        res2 = self.matcher.identify(addr2)
        self.assertEqual(res1, res2)
        self.assertEqual(
            res1,
            {
                "street": "Lê Lợi",
                "ward": "Phường Bến Thành",
                "province": "Thành phố Hồ Chí Minh",
            },
        )

    def test_missing_fields_recovery(self):
        res = self.matcher.identify("Đường Le Loj")
        self.assertEqual(res["street"], "Lê Lợi")
        self.assertIsNone(res["ward"])
        self.assertIsNone(res["province"])

    def test_repeated_malformed_tokens(self):
        res = self.matcher.identify("Đường Le Loj, Le Loj, Phường 1")
        # Duplicated candidate at distinct positions does not self-conflict
        self.assertEqual(res["street"], "Lê Lợi")
        self.assertEqual(res["ward"], "Phường 1")

    def test_ambiguous_equidistant_typo_stays_null(self):
        # Configure two equidistant streets
        matcher = AddressMatcher(
            streets=["Hòa Bình", "Hóa Binh"],
            wards=["Phường 1"],
            provinces=[],
        )
        res = matcher.identify("Đường Hoa Binh, Phường 1")
        self.assertIsNone(res["street"])
        self.assertEqual(res["ward"], "Phường 1")


class TestBudgetsAndTraceParity(unittest.TestCase):
    """Test budget bounds, search truncation abstention, and trace parity."""

    def test_strict_budgets_counts_under_limits(self):
        config = MatcherConfig(
            max_fuzzy_spans=10,
            max_candidates_per_span=5,
            max_verifications=15,
            max_postings=100,
            max_edits=2,
        )
        matcher = AddressMatcher(
            streets=["Lê Lợi", "Trần Hưng Đạo", "Nam Kỳ Khởi Nghĩa", "Điện Biên Phủ"],
            wards=["Phường Bến Thành"],
            provinces=["Thành phố Hồ Chí Minh"],
            config=config,
        )
        address = "Duong Le Loj, Phuong Ben Thnah, Thanh pho Ho Chi Minh"
        _, trace = matcher.identify_with_trace(address)
        budgets = trace["budgets"]

        for k in ("fuzzy_spans", "postings", "verifications", "candidates_per_span"):
            count = budgets["counts"][k]
            limit = budgets["limits"][k]
            self.assertLessEqual(
                count,
                limit,
                f"Budget {k} count {count} exceeded limit {limit}",
            )

    def test_exhausted_search_abstains_arbitrary_winner_and_preserves_exact(self):
        # Restrict verifications to 0 or 1 so search truncates on fuzzy street
        config = MatcherConfig(max_verifications=1)
        matcher = AddressMatcher(
            streets=["Điện Biên Phủ", "Điện Biên Phú"],
            wards=["Phường Bến Thành"],
            provinces=["Thành phố Hồ Chí Minh"],
            config=config,
        )
        # Typo street with exact province
        address = "Duong Dien Bien Pu, Thành phố Hồ Chí Minh"
        res, trace = matcher.identify_with_trace(address)

        # Truncated fuzzy street conservatively abstains
        self.assertIsNone(res["street"])
        # Independent exact province is preserved!
        self.assertEqual(res["province"], "Thành phố Hồ Chí Minh")

        budgets = trace["budgets"]
        self.assertTrue(budgets["exhausted"])

    def test_exhausted_search_abstains_suspicious_exact_prefix(self):
        # When budget is exhausted before resolving longer competitors,
        # exact prefix with suspicious tail is abstained instead of greedily winning
        config = MatcherConfig(max_postings=1)
        matcher = AddressMatcher(
            streets=["An Phú", "An Phú Đông"],
            wards=["Phường 1"],
            provinces=["Thành phố Hồ Chí Minh"],
            config=config,
        )
        address = "Đường An Phú Dnog, Phường 1, Thành phố Hồ Chí Minh"
        res, trace = matcher.identify_with_trace(address)

        # Suspicious exact prefix 'An Phú' cannot claim victory
        self.assertIsNone(res["street"])
        # Independent exact fields are preserved
        self.assertEqual(res["ward"], "Phường 1")
        self.assertEqual(res["province"], "Thành phố Hồ Chí Minh")

    def test_retrieved_fuzzy_candidates_appear_before_rejection(self):
        matcher = AddressMatcher(
            streets=["Nam Kỳ Khởi Nghĩa"],
            wards=["Phường Bến Thành"],
            provinces=["Thành phố Hồ Chí Minh"],
        )
        address = "Duong Le Loj"  # 'Le Loj' not in catalog, but trigrams might retrieve candidates
        _, trace = matcher.identify_with_trace(address)
        # Verify trace structure contains required keys
        self.assertIn("retrieved", trace)
        self.assertIn("verified", trace)
        self.assertIn("retained", trace)
        self.assertIn("selection", trace)
        self.assertIn("budgets", trace)

    def test_identify_and_traced_identify_identical(self):
        matcher = AddressMatcher(
            streets=["Nam Kỳ Khởi Nghĩa", "Lê Lợi", "3/2", "An Phú Đông"],
            wards=["Phường Bến Thành", "Phường 1", "An Phú"],
            provinces=["Thành phố Hồ Chí Minh", "Thành phố Hà Nội"],
        )
        test_inputs = [
            "Đường Le Loj, Phường 1",
            "123 phuong an phu dnog, Thanh pho Ho Chi Minh",
            "56/33 Nam Ky Khoi Ngiha, Phường Bến Thành",
            "Đường 3/3, Phường 1",
            "Đường L, Phường 1",
            "",
            "   ",
            ",,,",
        ]
        for addr in test_inputs:
            direct = matcher.identify(addr)
            traced, _ = matcher.identify_with_trace(addr)
            self.assertEqual(
                direct,
                traced,
                f"Mismatch between identify and identify_with_trace for {addr!r}",
            )


class TestFixtureEvaluationAndBenchmark(unittest.TestCase):
    """Benchmark recovery gains on fixture cases vs enable_fuzzy=False."""

    def test_fixture_cases_benchmark_and_negative_controls(self):
        with open(DICT_PATH, "r", encoding="utf-8") as f:
            dict_data = json.load(f)

        matcher_fuzzy = AddressMatcher(
            streets=dict_data["streets"],
            wards=dict_data["wards"],
            provinces=dict_data["provinces"],
            config=MatcherConfig(enable_fuzzy=True),
        )

        matcher_exact = AddressMatcher(
            streets=dict_data["streets"],
            wards=dict_data["wards"],
            provinces=dict_data["provinces"],
            config=MatcherConfig(enable_fuzzy=False),
        )

        cases = []
        with open(CASES_PATH, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    cases.append(json.loads(line))

        self.assertGreaterEqual(len(cases), 12)

        fuzzy_correct = 0
        exact_correct = 0

        for case in cases:
            addr = case["address"]
            expected = case["expected"]
            kind = case["expectation_kind"]

            res_fuzzy = matcher_fuzzy.identify(addr)
            res_exact = matcher_exact.identify(addr)

            if res_fuzzy == expected:
                fuzzy_correct += 1
            if res_exact == expected:
                exact_correct += 1

            # In negative controls, verify zero new false positives
            if "negative_control" in case["tags"]:
                for field in ("street", "ward", "province"):
                    if expected[field] is None:
                        self.assertIsNone(
                            res_fuzzy[field],
                            f"False positive in negative control {case['id']} for field {field}: {res_fuzzy[field]!r}",
                        )

        # Assert measurable gain over enable_fuzzy=False
        self.assertGreater(
            fuzzy_correct,
            exact_correct,
            f"Fuzzy correct ({fuzzy_correct}) must exceed exact correct ({exact_correct})",
        )
        self.assertEqual(
            fuzzy_correct,
            len(cases),
            f"Expected 100% accuracy on fixture cases, got {fuzzy_correct}/{len(cases)}",
        )


if __name__ == "__main__":
    unittest.main()
