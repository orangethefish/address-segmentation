"""Comprehensive tests for Stage C evidence, prefix-body forms, joint selection, and trace API."""

import json
import math
import unittest

from address_segmentation.catalog import Catalog
from address_segmentation.matcher import AddressMatcher
from address_segmentation.model import Candidate, MatcherConfig


class TestConfigurationValidation(unittest.TestCase):
    """Test MatcherConfig validation of numeric bounds, types, and nonfinite values."""

    def test_default_config_values(self):
        config = MatcherConfig()
        self.assertEqual(config.top_k, 8)
        self.assertEqual(config.ambiguity_margin, 0.0)
        self.assertEqual(config.min_score, 10.0)
        self.assertTrue(config.enable_fuzzy)
        self.assertFalse(config.enable_compact)
        self.assertFalse(config.enable_typing)

    def test_reject_bad_top_k(self):
        with self.assertRaises(ValueError):
            MatcherConfig(top_k=0)
        with self.assertRaises(ValueError):
            MatcherConfig(top_k=-1)
        with self.assertRaises(TypeError):
            MatcherConfig(top_k="8")  # type: ignore
        with self.assertRaises(TypeError):
            MatcherConfig(top_k=True)  # type: ignore (bool is subclass of int)
        with self.assertRaises(TypeError):
            MatcherConfig(top_k=8.0)  # type: ignore

    def test_reject_bad_ambiguity_margin(self):
        with self.assertRaises(ValueError):
            MatcherConfig(ambiguity_margin=-0.5)
        with self.assertRaises(ValueError):
            MatcherConfig(ambiguity_margin=float("nan"))
        with self.assertRaises(ValueError):
            MatcherConfig(ambiguity_margin=float("inf"))
        with self.assertRaises(ValueError):
            MatcherConfig(ambiguity_margin=-float("inf"))
        with self.assertRaises(TypeError):
            MatcherConfig(ambiguity_margin="0.0")  # type: ignore
        with self.assertRaises(TypeError):
            MatcherConfig(ambiguity_margin=False)  # type: ignore

    def test_reject_bad_min_score(self):
        with self.assertRaises(ValueError):
            MatcherConfig(min_score=float("nan"))
        with self.assertRaises(ValueError):
            MatcherConfig(min_score=float("inf"))
        with self.assertRaises(TypeError):
            MatcherConfig(min_score="10.0")  # type: ignore
        with self.assertRaises(TypeError):
            MatcherConfig(min_score=True)  # type: ignore

    def test_reject_bad_feature_flags(self):
        with self.assertRaises(TypeError):
            MatcherConfig(enable_fuzzy="true")  # type: ignore
        with self.assertRaises(TypeError):
            MatcherConfig(enable_compact=1)  # type: ignore
        with self.assertRaises(TypeError):
            MatcherConfig(enable_typing=None)  # type: ignore


class TestPrefixBodyAndMarkers(unittest.TestCase):
    """Test administrative prefix-body extraction, marker detection, and abbreviated forms."""

    @classmethod
    def setUpClass(cls):
        cls.matcher = AddressMatcher(
            streets=["Lê Lợi", "3/2", "Hòa Bình", "An Phú", "An Phú Đông"],
            wards=["Phường Bến Thành", "Phường 1", "Xã Tân Kiên", "Thị trấn Củ Chi"],
            provinces=["Thành phố Hồ Chí Minh", "Thành phố Hà Nội", "Tỉnh Hòa Bình", "Hòa Bình"],
        )

    def test_ward_abbreviated_marker(self):
        # "P. Bến Thành" matches canonical "Phường Bến Thành"
        result = self.matcher.identify("123 P. Bến Thành, Thành phố Hồ Chí Minh")
        self.assertEqual(result["ward"], "Phường Bến Thành")
        self.assertEqual(result["province"], "Thành phố Hồ Chí Minh")

    def test_ward_body_only(self):
        # Bare body match "Bến Thành" matches canonical "Phường Bến Thành"
        result = self.matcher.identify("123 Bến Thành, Thành phố Hồ Chí Minh")
        self.assertEqual(result["ward"], "Phường Bến Thành")
        self.assertEqual(result["province"], "Thành phố Hồ Chí Minh")

    def test_province_abbreviation(self):
        # "TP. Hồ Chí Minh" matches canonical "Thành phố Hồ Chí Minh"
        result = self.matcher.identify("Đường Lê Lợi, Phường 1, TP. Hồ Chí Minh")
        self.assertEqual(result["street"], "Lê Lợi")
        self.assertEqual(result["ward"], "Phường 1")
        self.assertEqual(result["province"], "Thành phố Hồ Chí Minh")

    def test_province_body_only(self):
        # Bare body match "Hồ Chí Minh" matches "Thành phố Hồ Chí Minh"
        result = self.matcher.identify("Đường Lê Lợi, Phường 1, Hồ Chí Minh")
        self.assertEqual(result["street"], "Lê Lợi")
        self.assertEqual(result["ward"], "Phường 1")
        self.assertEqual(result["province"], "Thành phố Hồ Chí Minh")

    def test_street_marker_strengthening(self):
        # Street marker 'Đường' before 'Lê Lợi'
        result = self.matcher.identify("Đường Lê Lợi, Phường 1")
        self.assertEqual(result["street"], "Lê Lợi")
        self.assertEqual(result["ward"], "Phường 1")

    def test_abbreviated_street_marker(self):
        # 'Đ. Lê Lợi' abbreviated street marker
        result = self.matcher.identify("Đ. Lê Lợi, Phường 1")
        self.assertEqual(result["street"], "Lê Lợi")
        self.assertEqual(result["ward"], "Phường 1")

    def test_field_marker_disambiguates_shared_span(self):
        # 'Đường Hòa Bình' has street marker, disambiguating against province 'Hòa Bình'
        res_street = self.matcher.identify("123 Đường Hòa Bình, Phường 1")
        self.assertEqual(res_street["street"], "Hòa Bình")
        self.assertIsNone(res_street["province"])
        self.assertEqual(res_street["ward"], "Phường 1")

        # 'Tỉnh Hòa Bình' has province marker, disambiguating against street 'Hòa Bình'
        res_prov = self.matcher.identify("123 Tỉnh Hòa Bình, Phường 1")
        self.assertIsNone(res_prov["street"])
        self.assertEqual(res_prov["province"], "Tỉnh Hòa Bình")
        self.assertEqual(res_prov["ward"], "Phường 1")


class TestJointSelectionRules(unittest.TestCase):
    """Test joint selection: compatibility, ambiguity, superseding, and repetition."""

    @classmethod
    def setUpClass(cls):
        cls.matcher = AddressMatcher(
            streets=["Lê Lợi", "Trần Hưng Đạo", "Hòa Bình", "An Phú", "An Phú Đông"],
            wards=["Phường 1", "Phường 2", "Phường Bến Thành"],
            provinces=["Thành phố Hồ Chí Minh", "Thành phố Hà Nội", "Hòa Bình"],
        )

    def test_shared_unmarked_span_returns_null(self):
        # '123 Hòa Bình, Phường 1': 'Hòa Bình' matches both street and province without marker
        # Both must null; independent third field 'Phường 1' is retained
        result = self.matcher.identify("123 Hòa Bình, Phường 1")
        self.assertIsNone(result["street"])
        self.assertIsNone(result["province"])
        self.assertEqual(result["ward"], "Phường 1")

    def test_repeated_same_label_at_distinct_spans_fills_fields(self):
        # 'Hòa Bình' appears twice at distinct non-overlapping spans:
        # First as street, second as province. Both fields must be filled.
        result = self.matcher.identify("Đường Hòa Bình, Tỉnh Hòa Bình")
        self.assertEqual(result["street"], "Hòa Bình")
        self.assertEqual(result["province"], "Hòa Bình")
        self.assertIsNone(result["ward"])

    def test_overlapping_cross_field_candidates_incompatible(self):
        # A single span 'Hòa Bình' cannot support both street and province
        result = self.matcher.identify("123 Hòa Bình")
        self.assertIsNone(result["street"])
        self.assertIsNone(result["province"])
        self.assertIsNone(result["ward"])

    def test_longer_same_field_exact_name_wins_over_contained_prefix(self):
        # 'An Phú Đông' contains 'An Phú' as a prefix at the same occurrence
        # The longer complete exact name must supersede the contained prefix
        result = self.matcher.identify("123 Đường An Phú Đông, Phường 1")
        self.assertEqual(result["street"], "An Phú Đông")
        self.assertEqual(result["ward"], "Phường 1")

        result_bare = self.matcher.identify("123 An Phú Đông, Phường 1")
        self.assertEqual(result_bare["street"], "An Phú Đông")

    def test_non_overlapping_equal_competing_streets_still_null(self):
        # Two unrelated streets in the same address abstain regardless of length/order
        r1 = self.matcher.identify("Góc đường Lê Lợi và Trần Hưng Đạo, Phường 1")
        self.assertIsNone(r1["street"])
        self.assertEqual(r1["ward"], "Phường 1")

        r2 = self.matcher.identify("Đường Lê Lợi, Đường Trần Hưng Đạo, Phường 1")
        self.assertIsNone(r2["street"])
        self.assertEqual(r2["ward"], "Phường 1")

        r3 = self.matcher.identify("Lê Lợi, Trần Hưng Đạo, Phường 1")
        self.assertIsNone(r3["street"])
        self.assertEqual(r3["ward"], "Phường 1")

    def test_ambiguity_preserves_province(self):
        # Ambiguous competing streets do not contaminate a reliable province
        result = self.matcher.identify(
            "Góc đường Lê Lợi và Trần Hưng Đạo, Thành phố Hồ Chí Minh"
        )
        self.assertIsNone(result["street"])
        self.assertIsNone(result["ward"])
        self.assertEqual(result["province"], "Thành phố Hồ Chí Minh")

    def test_component_order_permutations(self):
        # Component order permutations yield the same segmented fields
        addr_standard = "Đường Lê Lợi, Phường Bến Thành, Thành phố Hồ Chí Minh"
        addr_permuted = "Thành phố Hồ Chí Minh, Phường Bến Thành, Đường Lê Lợi"
        res_standard = self.matcher.identify(addr_standard)
        res_permuted = self.matcher.identify(addr_permuted)
        self.assertEqual(res_standard, res_permuted)
        self.assertEqual(
            res_standard,
            {
                "street": "Lê Lợi",
                "ward": "Phường Bến Thành",
                "province": "Thành phố Hồ Chí Minh",
            },
        )


class TestTopKPruningAndTraceAPI(unittest.TestCase):
    """Test top-K conservative pruning, trace serialization, and trace-identify agreement."""

    def test_top_k_pruning_boundary_and_non_biasing(self):
        # Configure small top_k=2 with 3 competing street candidates
        config = MatcherConfig(top_k=2)
        matcher = AddressMatcher(
            streets=["Lê Lợi", "Trần Hưng Đạo", "Nam Kỳ Khởi Nghĩa"],
            wards=["Phường 1"],
            provinces=["Thành phố Hà Nội"],
            config=config,
        )
        address = "Lê Lợi, Trần Hưng Đạo, Nam Kỳ Khởi Nghĩa, Phường 1, Thành phố Hà Nội"
        result, trace = matcher.identify_with_trace(address)

        # Street had 3 candidates > top_k=2, so street is pruned and nulled before scoring
        self.assertIsNone(result["street"])
        self.assertEqual(result["ward"], "Phường 1")
        self.assertEqual(result["province"], "Thành phố Hà Nội")

        selection = trace["selection"]
        self.assertIn("street", selection["pruned_fields"])
        self.assertEqual(selection["rejection_reasons"]["street"], "pruned")
        self.assertNotIn("ward", selection["pruned_fields"])
        self.assertNotIn("province", selection["pruned_fields"])

    def test_trace_json_serializable_and_valid_spans(self):
        matcher = AddressMatcher(
            streets=["Nam Kỳ Khởi Nghĩa", "Lê Lợi"],
            wards=["Phường Bến Thành"],
            provinces=["Thành phố Hồ Chí Minh"],
        )
        address = "56/33 Nam Kỳ Khởi Nghĩa, Phường Bến Thành, Thành phố Hồ Chí Minh"
        result, trace = matcher.identify_with_trace(address)

        # Must serialize cleanly to JSON
        json_str = json.dumps(trace, ensure_ascii=False)
        self.assertIsInstance(json_str, str)

        # Check required top-level trace keys
        self.assertIn("retrieved", trace)
        self.assertIn("verified", trace)
        self.assertIn("retained", trace)
        self.assertIn("selection", trace)

        selection = trace["selection"]
        self.assertIn("best_score", selection)
        self.assertIn("ambiguous_fields", selection)
        self.assertIn("pruned_fields", selection)
        self.assertIn("rejection_reasons", selection)

        # Verify candidate dictionaries structure and span boundaries
        for stage_key in ("retrieved", "verified", "retained"):
            cands = trace[stage_key]
            for cand in cands:
                for k in ("field", "label", "span", "path", "cost", "score"):
                    self.assertIn(k, cand)
                start, end = cand["span"]
                self.assertGreaterEqual(start, 0)
                self.assertLessEqual(end, len(address))
                self.assertLess(start, end)
                # Verify that address slice is non-empty
                matched_slice = address[start:end]
                self.assertTrue(len(matched_slice) > 0)

    def test_identify_and_traced_identify_agree(self):
        matcher = AddressMatcher(
            streets=["Nam Kỳ Khởi Nghĩa", "Lê Lợi", "3/2"],
            wards=["Phường Bến Thành", "Phường 1"],
            provinces=["Thành phố Hồ Chí Minh", "Thành phố Hà Nội"],
        )
        test_addresses = [
            "56/33 Nam Kỳ Khởi Nghĩa, Phường Bến Thành, Thành phố Hồ Chí Minh",
            "Đường 3/2, Phường 1",
            "Góc đường Lê Lợi và 3/2, Thành phố Hồ Chí Minh",
            "123 Địa chỉ không tồn tại",
            "",
            "   \t\n  ",
            ",,,",
        ]
        for addr in test_addresses:
            direct_res = matcher.identify(addr)
            traced_res, _ = matcher.identify_with_trace(addr)
            self.assertEqual(
                direct_res,
                traced_res,
                f"Mismatch between identify and identify_with_trace on input: {addr!r}",
            )


if __name__ == "__main__":
    unittest.main()
