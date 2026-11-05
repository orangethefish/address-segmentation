"""Integration evidence and negative controls discovered from the user dataset."""

import json
from pathlib import Path
import tempfile
import unittest
import unicodedata

from address_segmentation import AddressMatcher
from address_segmentation.model import MatcherConfig
from tools.datasets import load_cases
from tools.prepare_dataset import prepare_dataset

SOURCE = Path(__file__).resolve().parents[1] / "testcases" / "1"


class MatchingReviewTests(unittest.TestCase):
    def test_marker_types_disambiguate_shared_ward_bodies(self):
        matcher = AddressMatcher(wards=["Phường Hải Tân", "Xã Hải Tân", "Thị trấn Hải Tân"])
        for marker, expected in [("P.", "Phường Hải Tân"), ("x.", "Xã Hải Tân"),
                                 ("xa", "Xã Hải Tân"), ("TT.", "Thị trấn Hải Tân")]:
            for body in ("Hải Tân", "Hai Tan"):
                with self.subTest(marker=marker, body=body):
                    self.assertEqual(matcher.identify(marker + body if marker.endswith('.') else marker + ' ' + body)["ward"], expected)
        self.assertIsNone(matcher.identify("Hai Tan")["ward"])

    def test_mismatched_marker_does_not_create_support(self):
        matcher = AddressMatcher(wards=["Phường Hải Tân"])
        for address in ("X. Hải Tân", "xa hai tan", "X.Hai Tna", "X.HaiTan", "X. Hair Taan"):
            with self.subTest(address=address):
                self.assertIsNone(matcher.identify(address)["ward"])

    def test_marker_type_applies_to_fuzzy_compact_and_typing(self):
        matcher = AddressMatcher(wards=["Phường Hải Tân", "Xã Hải Tân"])
        for address in ("X. Hai Tna", "X.HaiTan", "X. Hair Taan"):
            with self.subTest(address=address):
                self.assertEqual(matcher.identify(address)["ward"], "Xã Hải Tân")

    def test_province_marker_types_remain_distinct(self):
        matcher = AddressMatcher(provinces=["Tỉnh Hải Tân", "Thành phố Hải Tân"])
        self.assertEqual(matcher.identify("TP. Hai Tan")["province"], "Thành phố Hải Tân")
        self.assertEqual(matcher.identify("T. Hai Tan")["province"], "Tỉnh Hải Tân")

    def test_province_initials_are_derived_and_collision_safe(self):
        matcher = AddressMatcher(provinces=["Hà Nội", "Thành phố Hồ Chí Minh"])
        for text, expected in [("HN", "Hà Nội"), ("TP.HCM", "Thành phố Hồ Chí Minh"),
                               ("HCM", "Thành phố Hồ Chí Minh")]:
            self.assertEqual(matcher.identify(text)["province"], expected)
        self.assertIsNone(AddressMatcher(provinces=["Hà Nội", "Hải Nam"]).identify("HN")["province"])
        self.assertEqual(AddressMatcher(provinces=["Hà Nội", "HN"]).identify("HN")["province"], "HN")
        self.assertIsNone(AddressMatcher(provinces=["Hà Nội"]).identify("xHNx")["province"])
        self.assertIsNone(AddressMatcher(provinces=["Hà Nội"]).identify("HCM")["province"])

    def test_contained_suffix_does_not_replace_complete_name(self):
        matcher = AddressMatcher(streets=["Nguyễn Thị Minh Khai", "Minh Khai"],
                                 wards=["Phường Minh Khai", "Xã Minh Khai"])
        for street in ("Nguyễn Thị Minh Khai", "Nguyen Thi Minh Khai"):
            self.assertEqual(matcher.identify(street),
                             dict(street="Nguyễn Thị Minh Khai", ward=None, province=None))
            self.assertEqual(matcher.identify(street + ", P. Minh Khai")["ward"], "Phường Minh Khai")
        self.assertEqual(matcher.identify("Minh Khai")["street"], None)  # Shared standalone span.

    def test_longer_name_cannot_erase_conflicting_exact_accents(self):
        matcher = AddressMatcher(streets=["Nguyễn Hóa Bình", "Hòa Bình"])
        self.assertEqual(matcher.identify("nguyen Hòa Bình")["street"], "Hòa Bình")

    def test_components_cannot_match_across_separators(self):
        matcher = AddressMatcher(streets=["Nguyễn Huệ"], wards=["Phường Vĩnh Nguyên"], provinces=["Huế"])
        for separator in (",", ";", "\n"):
            result = matcher.identify("Phuong Vinh Nguyen" + separator + " Hue")
            self.assertEqual(result, dict(street=None, ward="Phường Vĩnh Nguyên", province="Huế"))

    def test_compact_cannot_overwrite_explicit_accents(self):
        config = MatcherConfig(enable_fuzzy=False, enable_typing=False)
        for observed, target in [("TấnAn", "Tân An"), ("ĐôngHoa", "Dông Hoa"),
                                 ("Hòaan", "Hoa Àn")]:
            matcher = AddressMatcher(streets=[target], config=config)
            self.assertIsNone(matcher.identify(observed)["street"])
            self.assertIsNone(matcher.identify(unicodedata.normalize("NFD", observed))["street"])
        self.assertEqual(AddressMatcher(streets=["Tấn An"], config=config).identify("TanAn")["street"], "Tấn An")

    def test_numbered_street_body_cannot_claim_a_house_number(self):
        matcher = AddressMatcher(streets=["Đường số 123", "Đường 123", "Hòa Bình"],
                                 wards=["Phường 88"], provinces=["Hòa Bình"])
        self.assertEqual(matcher.identify("Số 123 Hòa Bình, Phường 88"),
                         dict(street=None, ward="Phường 88", province=None))
        self.assertIsNone(matcher.identify("Số 123 Cung Đường Ngoại Lai")["street"])
        self.assertEqual(matcher.identify("Đường số 123")["street"], "Đường số 123")
        self.assertEqual(matcher.identify("Đường 123")["street"], "Đường 123")

    def test_long_repetitions_with_noise_keep_independent_evidence(self):
        matcher = AddressMatcher(streets=["Lê Lợi"], provinces=["Hà Nội"])
        address = "Lê Lợi, " * 200 + "qzxv, Hà Nội"
        result, trace = matcher.identify_with_trace(address)
        self.assertEqual(result["province"], "Hà Nội")
        self.assertEqual(result, matcher.identify(address))
        self.assertIn("street", trace["selection"]["pruned_fields"])
        for name, limit in trace["budgets"]["limits"].items():
            self.assertLessEqual(trace["budgets"]["counts"][name], limit)


class DatasetAdapterTests(unittest.TestCase):
    def test_user_dataset_against_reviewed_expectations(self):
        source_bytes = {p.name: p.read_bytes() for p in SOURCE.iterdir() if p.is_file()}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            manifest = prepare_dataset(SOURCE, out, SOURCE / "review.json")
            dictionary = json.loads((out / "dictionary.json").read_text(encoding="utf-8"))
            cases = load_cases(out / "cases.jsonl", dictionary)
            raw_cases = load_cases(out / "raw-cases.jsonl", dictionary)
            self.assertEqual(manifest["counts"], dict(streets=162, wards=319, provinces=34, cases=1000))
            self.assertEqual(manifest["reviewed_fields"], 6)
            matcher = AddressMatcher(**dictionary)
            for case in cases:
                with self.subTest(case=case.id):
                    self.assertEqual(matcher.identify(case.address), case.expected)
            self.assertEqual(sum(a.expected != b.expected for a, b in zip(cases, raw_cases)), 6)
            self.assertEqual(prepare_dataset(SOURCE, out, SOURCE / "review.json"), manifest)
        self.assertEqual(source_bytes, {p.name: p.read_bytes() for p in SOURCE.iterdir() if p.is_file()})

    def test_no_review_preserves_original_expectations(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            manifest = prepare_dataset(SOURCE, out)
            self.assertEqual(manifest["reviewed_fields"], 0)
            self.assertEqual((out / "cases.jsonl").read_bytes(), (out / "raw-cases.jsonl").read_bytes())

    def test_stale_or_invalid_review_is_rejected_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            items = json.loads((SOURCE / "review.json").read_text(encoding="utf-8"))
            items[0]["original"] = "Phường Long Phước"
            review = root / "review.json"
            review.write_text(json.dumps(items), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "accent collision"):
                prepare_dataset(SOURCE, root / "output", review)
            self.assertFalse((root / "output").exists())

    def test_reject_output_in_raw_source(self):
        with self.assertRaises(ValueError):
            prepare_dataset(SOURCE, SOURCE)

    def test_adapter_rejects_invalid_labels_and_conflicting_duplicate_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "raw"
            source.mkdir()
            for field in ("street", "ward", "province"):
                (source / f"list_{field}.txt").write_text("Known\n", encoding="utf-8")
            record = dict(address="Known", result=dict(street="Missing", ward=None, province=None))
            raw = source / "all_raw.json"
            raw.write_text(json.dumps([record]), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid canonical"):
                prepare_dataset(source, root / "output")
            record["result"]["street"] = "Known"
            second = dict(address="Known", result=dict(street=None, ward=None, province=None))
            raw.write_text(json.dumps([record, second]), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "conflicting expectations"):
                prepare_dataset(source, root / "output")
