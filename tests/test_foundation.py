"""Comprehensive tests for Stage A foundation: catalog, normalization, matcher, CLI, and contracts."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import unittest

from address_segmentation.catalog import Catalog, CatalogEntry
from address_segmentation.matcher import AddressMatcher
from address_segmentation.normalize import fold_accents, tokenize, tokenize_label

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "dictionary.json"


def get_cli_env() -> dict[str, str]:
    """Return environment dictionary with REPO_ROOT prepended to PYTHONPATH."""
    env = os.environ.copy()
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{REPO_ROOT}{os.pathsep}{existing_pp}" if existing_pp else str(REPO_ROOT)
    )
    return env


class TestCatalogAndContract(unittest.TestCase):
    """Test catalog sequence validation, deduplication, stable IDs, and edge cases."""

    def test_reject_scalar_string_as_sequence(self):
        with self.assertRaises(TypeError):
            AddressMatcher(streets="Nam Kỳ Khởi Nghĩa")
        with self.assertRaises(TypeError):
            AddressMatcher(wards=b"Phuong 1")

    def test_reject_non_sequence(self):
        with self.assertRaises(TypeError):
            AddressMatcher(streets=123)
        with self.assertRaises(TypeError):
            AddressMatcher(provinces=None)

    def test_reject_non_string_items(self):
        with self.assertRaises(TypeError):
            AddressMatcher(streets=["Lê Lợi", 123])
        with self.assertRaises(TypeError):
            AddressMatcher(wards=[None])

    def test_reject_blank_or_whitespace_labels(self):
        with self.assertRaises(ValueError):
            AddressMatcher(streets=[""])
        with self.assertRaises(ValueError):
            AddressMatcher(streets=["   \t\n  "])
        with self.assertRaises(ValueError):
            AddressMatcher(streets=[", , ,"])

    def test_deduplicate_identical_labels(self):
        matcher = AddressMatcher(
            streets=["Lê Lợi", "Lê Lợi", "Lê Lợi"],
            wards=["Phường 1"],
            provinces=["Thành phố Hồ Chí Minh"],
        )
        result = matcher.identify("Đường Lê Lợi, Phường 1, Thành phố Hồ Chí Minh")
        self.assertEqual(result["street"], "Lê Lợi")
        self.assertEqual(result["ward"], "Phường 1")
        self.assertEqual(result["province"], "Thành phố Hồ Chí Minh")

    def test_stable_behavior_under_dictionary_permutation(self):
        matcher_a = AddressMatcher(
            streets=["Nam Kỳ Khởi Nghĩa", "Lê Lợi", "3/2"],
            wards=["Phường Bến Thành", "Phường 1"],
            provinces=["Thành phố Hồ Chí Minh", "Thành phố Hà Nội"],
        )
        matcher_b = AddressMatcher(
            streets=["3/2", "Lê Lợi", "Nam Kỳ Khởi Nghĩa"],
            wards=["Phường 1", "Phường Bến Thành"],
            provinces=["Thành phố Hà Nội", "Thành phố Hồ Chí Minh"],
        )
        addresses = [
            "56/33 Nam Kỳ Khởi Nghĩa, Phường Bến Thành, Thành phố Hồ Chí Minh",
            "Đường 3/2, Phường 1",
            "Lê Lợi, Thành phố Hà Nội",
        ]
        for addr in addresses:
            self.assertEqual(matcher_a.identify(addr), matcher_b.identify(addr))

    def test_lexicographical_id_assignment_and_public_result_order(self):
        catalog = Catalog(
            streets=["Lê Lợi"],
            wards=["Phường 1"],
            provinces=["Thành phố Hà Nội"],
        )
        # Entry IDs sorted by (field, canonical_label) where 'province' < 'street' < 'ward'
        entries = catalog.entries
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[0].field, "province")
        self.assertEqual(entries[0].entry_id, 0)
        self.assertEqual(entries[1].field, "street")
        self.assertEqual(entries[1].entry_id, 1)
        self.assertEqual(entries[2].field, "ward")
        self.assertEqual(entries[2].entry_id, 2)

        matcher = AddressMatcher(
            streets=["Lê Lợi"],
            wards=["Phường 1"],
            provinces=["Thành phố Hà Nội"],
        )
        res = matcher.identify("Lê Lợi, Phường 1, Thành phố Hà Nội")
        # Public result keys must preserve exact order: street, ward, province
        self.assertEqual(list(res.keys()), ["street", "ward", "province"])

    def test_catalog_immutability_and_tamper_resistance(self):
        catalog = Catalog(
            streets=["Lê Lợi"],
            wards=["Phường 1"],
            provinces=["Thành phố Hà Nội"],
        )
        # Verify immutable return types
        self.assertIsInstance(catalog.token_lengths, frozenset)
        exact_postings = catalog.lookup_exact(("lê", "lợi"))
        self.assertIsInstance(exact_postings, tuple)
        folded_postings = catalog.lookup_folded(("le", "loi"))
        self.assertIsInstance(folded_postings, tuple)

        matcher = AddressMatcher(
            streets=["Lê Lợi"],
            wards=["Phường 1"],
            provinces=["Thành phố Hà Nội"],
        )
        # Callers cannot alter internal structures
        before = matcher.identify("Lê Lợi")
        self.assertEqual(before["street"], "Lê Lợi")

    def test_empty_dictionaries_allowed(self):
        matcher = AddressMatcher(streets=[], wards=[], provinces=[])
        result = matcher.identify("56/33 Nam Kỳ Khởi Nghĩa, Phường Bến Thành, Thành phố Hồ Chí Minh")
        self.assertEqual(result, {"street": None, "ward": None, "province": None})

    def test_nonstring_address_raises_type_error(self):
        matcher = AddressMatcher(streets=["Lê Lợi"])
        with self.assertRaises(TypeError):
            matcher.identify(123)  # type: ignore
        with self.assertRaises(TypeError):
            matcher.identify(None)  # type: ignore
        with self.assertRaises(TypeError):
            matcher.identify(["Lê Lợi"])  # type: ignore

    def test_fresh_result_dictionary(self):
        matcher = AddressMatcher(streets=["Lê Lợi"])
        r1 = matcher.identify("Lê Lợi")
        r2 = matcher.identify("Lê Lợi")
        self.assertEqual(r1, r2)
        self.assertIsNot(r1, r2)
        r1["street"] = "Modified"
        self.assertEqual(r2["street"], "Lê Lợi")


class TestNormalizationAndTokens(unittest.TestCase):
    """Test Unicode NFC/NFD, accent folding, đ/Đ handling, token original slices, and offsets."""

    def test_accent_folding(self):
        self.assertEqual(fold_accents("Đà Nẵng"), "da nang")
        self.assertEqual(fold_accents("Hồ Chí Minh"), "ho chi minh")
        self.assertEqual(fold_accents("Điện Biên Phủ"), "dien bien phu")
        self.assertEqual(fold_accents("3/2"), "3/2")

    def test_token_original_slices_and_combining_marks(self):
        raw_address = "56/33 Nam Kỳ Khởi Nghĩa, Phường Bến Thành, Thành phố Hồ Chí Minh"
        nfd_address = unicodedata.normalize("NFD", raw_address)
        tokens = tokenize(nfd_address)

        # Every token slice text[start:end] must exactly equal token.text
        for tok in tokens:
            self.assertEqual(nfd_address[tok.start:tok.end], tok.text)

        # In NFD, 'Khởi' contains combining marks that must stay attached to their base letters
        khoi_tokens = [t for t in tokens if t.nfc == "khởi"]
        self.assertEqual(len(khoi_tokens), 1)
        self.assertGreater(len(khoi_tokens[0].text), 4)  # Decomposed length is longer than 4
        self.assertEqual(nfd_address[khoi_tokens[0].start:khoi_tokens[0].end], khoi_tokens[0].text)

    def test_decomposed_nfd_input_matches(self):
        matcher = AddressMatcher(
            streets=["Nam Kỳ Khởi Nghĩa"],
            wards=["Phường Bến Thành"],
            provinces=["Thành phố Hồ Chí Minh"],
        )
        nfd_input = unicodedata.normalize(
            "NFD", "56/33 Nam Kỳ Khởi Nghĩa, Phường Bến Thành, Thành phố Hồ Chí Minh"
        )
        result = matcher.identify(nfd_input)
        self.assertEqual(result["street"], "Nam Kỳ Khởi Nghĩa")
        self.assertEqual(result["ward"], "Phường Bến Thành")
        self.assertEqual(result["province"], "Thành phố Hồ Chí Minh")

    def test_nfd_canonical_dictionary_labels(self):
        # Dictionaries configured with decomposed NFD strings
        nfd_street = unicodedata.normalize("NFD", "Nam Kỳ Khởi Nghĩa")
        nfd_ward = unicodedata.normalize("NFD", "Phường Bến Thành")
        nfd_prov = unicodedata.normalize("NFD", "Thành phố Hồ Chí Minh")

        matcher = AddressMatcher(
            streets=[nfd_street],
            wards=[nfd_ward],
            provinces=[nfd_prov],
        )

        # Matching against both precomposed NFC and decomposed NFD inputs
        res_nfc = matcher.identify("56/33 Nam Kỳ Khởi Nghĩa, Phường Bến Thành, Thành phố Hồ Chí Minh")
        self.assertEqual(res_nfc["street"], nfd_street)
        self.assertEqual(res_nfc["ward"], nfd_ward)
        self.assertEqual(res_nfc["province"], nfd_prov)

        res_nfd = matcher.identify(
            unicodedata.normalize("NFD", "56/33 Nam Kỳ Khởi Nghĩa, Phường Bến Thành, Thành phố Hồ Chí Minh")
        )
        self.assertEqual(res_nfd["street"], nfd_street)
        self.assertEqual(res_nfd["ward"], nfd_ward)
        self.assertEqual(res_nfd["province"], nfd_prov)

    def test_d_stroke_folding(self):
        matcher = AddressMatcher(provinces=["Thành phố Đà Nẵng"])
        result1 = matcher.identify("Thanh pho Da Nang")
        self.assertEqual(result1["province"], "Thành phố Đà Nẵng")
        result2 = matcher.identify("thành phố đa nẵng")
        self.assertEqual(result2["province"], "Thành phố Đà Nẵng")


class TestMatcherBehavior(unittest.TestCase):
    """Test matching semantics, competition, cross-field spans, and boundaries."""

    @classmethod
    def setUpClass(cls):
        with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        cls.matcher = AddressMatcher(
            streets=data["streets"],
            wards=data["wards"],
            provinces=data["provinces"],
        )

    def test_canonical_exact_full_address(self):
        addr = "56/33 Nam Kỳ Khởi Nghĩa, Phường Bến Thành, Thành phố Hồ Chí Minh"
        result = self.matcher.identify(addr)
        self.assertEqual(
            result,
            {
                "street": "Nam Kỳ Khởi Nghĩa",
                "ward": "Phường Bến Thành",
                "province": "Thành phố Hồ Chí Minh",
            },
        )

    def test_missing_fields(self):
        r1 = self.matcher.identify("Thành phố Hà Nội")
        self.assertEqual(r1, {"street": None, "ward": None, "province": "Thành phố Hà Nội"})

        r2 = self.matcher.identify("Đường Lê Lợi, Thành phố Hà Nội")
        self.assertEqual(r2, {"street": "Lê Lợi", "ward": None, "province": "Thành phố Hà Nội"})

        r3 = self.matcher.identify("Phường Bến Thành")
        self.assertEqual(r3, {"street": None, "ward": "Phường Bến Thành", "province": None})

    def test_empty_and_punctuation_inputs(self):
        for empty_text in ("", "   ", "\t\n", ",", ", , ,", "...", "!?:;"):
            result = self.matcher.identify(empty_text)
            self.assertEqual(result, {"street": None, "ward": None, "province": None})

    def test_numeric_boundary_house_numbers(self):
        # House number 53/2 must NOT match street 3/2
        addr = "53/2 Nam Kỳ Khởi Nghĩa, Phường Bến Thành, Thành phố Hồ Chí Minh"
        result = self.matcher.identify(addr)
        self.assertEqual(result["street"], "Nam Kỳ Khởi Nghĩa")

        # Explicit street 3/2
        addr_3_2 = "Đường 3/2, Phường 1, Thành phố Hồ Chí Minh"
        result_3_2 = self.matcher.identify(addr_3_2)
        self.assertEqual(result_3_2["street"], "3/2")

    def test_exact_accented_outranks_folded(self):
        matcher = AddressMatcher(
            streets=["Hòa Bình", "Hoa Binh"],
            wards=[],
            provinces=[],
        )
        result = matcher.identify("123 đường Hòa Bình")
        self.assertEqual(result["street"], "Hòa Bình")

    def test_unresolved_same_field_competing_labels(self):
        addr = "Góc đường Lê Lợi và Trần Hưng Đạo, Phường 1"
        result = self.matcher.identify(addr)
        self.assertIsNone(result["street"])
        self.assertEqual(result["ward"], "Phường 1")

    def test_shared_field_span_conflict(self):
        addr = "123 Hòa Bình, Phường 1"
        result = self.matcher.identify(addr)
        self.assertIsNone(result["street"])
        self.assertIsNone(result["province"])
        self.assertEqual(result["ward"], "Phường 1")

    def test_accent_folded_collision_abstention(self):
        matcher = AddressMatcher(
            streets=["Hòa Bình", "Hóa Binh"],
            wards=[],
            provinces=[],
        )
        result = matcher.identify("123 Hoa Binh")
        self.assertIsNone(result["street"])


class TestCLIIntegration(unittest.TestCase):
    """Test CLI subprocess execution, stdin/stdout streaming, relative paths, and error exits."""

    def test_cli_success(self):
        cmd = [
            sys.executable,
            "-m",
            "address_segmentation.io",
            "--dictionary",
            str(FIXTURE_PATH),
        ]
        input_data = (
            json.dumps("56/33 Nam Kỳ Khởi Nghĩa, Phường Bến Thành, Thành phố Hồ Chí Minh")
            + "\n"
            + json.dumps("Đường 3/2, Phường 1")
            + "\n"
        )
        proc = subprocess.run(
            cmd,
            input=input_data,
            capture_output=True,
            text=True,
            env=get_cli_env(),
            check=False,
        )
        self.assertEqual(proc.returncode, 0, f"CLI stderr: {proc.stderr}")
        lines = [json.loads(line) for line in proc.stdout.strip().splitlines()]
        self.assertEqual(len(lines), 2)
        self.assertEqual(
            lines[0],
            {
                "street": "Nam Kỳ Khởi Nghĩa",
                "ward": "Phường Bến Thành",
                "province": "Thành phố Hồ Chí Minh",
            },
        )
        self.assertEqual(
            lines[1],
            {
                "street": "3/2",
                "ward": "Phường 1",
                "province": None,
            },
        )

    def test_cli_empty_input_stream_eof(self):
        cmd = [
            sys.executable,
            "-m",
            "address_segmentation.io",
            "--dictionary",
            str(FIXTURE_PATH),
        ]
        proc = subprocess.run(
            cmd,
            input="",
            capture_output=True,
            text=True,
            env=get_cli_env(),
            check=False,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")

    def test_cli_json_empty_string_produces_nulls(self):
        cmd = [
            sys.executable,
            "-m",
            "address_segmentation.io",
            "--dictionary",
            str(FIXTURE_PATH),
        ]
        proc = subprocess.run(
            cmd,
            input=json.dumps("") + "\n",
            capture_output=True,
            text=True,
            env=get_cli_env(),
            check=False,
        )
        self.assertEqual(proc.returncode, 0)
        res = json.loads(proc.stdout.strip())
        self.assertEqual(res, {"street": None, "ward": None, "province": None})

    def test_cli_blank_record_rejected(self):
        cmd = [
            sys.executable,
            "-m",
            "address_segmentation.io",
            "--dictionary",
            str(FIXTURE_PATH),
        ]
        proc = subprocess.run(
            cmd,
            input="\n",
            capture_output=True,
            text=True,
            env=get_cli_env(),
            check=False,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("blank line", proc.stderr)

    def test_cli_malformed_input_json(self):
        cmd = [
            sys.executable,
            "-m",
            "address_segmentation.io",
            "--dictionary",
            str(FIXTURE_PATH),
        ]
        proc = subprocess.run(
            cmd,
            input="NOT_A_VALID_JSON_STRING\n",
            capture_output=True,
            text=True,
            env=get_cli_env(),
            check=False,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Malformed JSON input", proc.stderr)

    def test_cli_non_string_json_input(self):
        cmd = [
            sys.executable,
            "-m",
            "address_segmentation.io",
            "--dictionary",
            str(FIXTURE_PATH),
        ]
        proc = subprocess.run(
            cmd,
            input=json.dumps(12345) + "\n",
            capture_output=True,
            text=True,
            env=get_cli_env(),
            check=False,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Expected JSON string", proc.stderr)

    def test_cli_missing_dictionary_file(self):
        cmd = [
            sys.executable,
            "-m",
            "address_segmentation.io",
            "--dictionary",
            "nonexistent_dictionary_path.json",
        ]
        proc = subprocess.run(
            cmd,
            input=json.dumps("Nam Kỳ Khởi Nghĩa") + "\n",
            capture_output=True,
            text=True,
            env=get_cli_env(),
            check=False,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Dictionary error", proc.stderr)

    def test_cli_relative_path_from_cwd(self):
        # Run from tests/ directory with relative fixture path, ensuring PYTHONPATH works
        cwd = FIXTURE_PATH.parent.parent  # tests directory
        rel_path = Path("fixtures") / "dictionary.json"
        cmd = [
            sys.executable,
            "-m",
            "address_segmentation.io",
            "--dictionary",
            str(rel_path),
        ]
        proc = subprocess.run(
            cmd,
            input=json.dumps("Thành phố Hồ Chí Minh") + "\n",
            capture_output=True,
            text=True,
            cwd=str(cwd),
            env=get_cli_env(),
            check=False,
        )
        self.assertEqual(proc.returncode, 0, f"CLI stderr: {proc.stderr}")
        res = json.loads(proc.stdout.strip())
        self.assertEqual(res["province"], "Thành phố Hồ Chí Minh")

    def test_cli_path_with_spaces(self):
        with tempfile.TemporaryDirectory(prefix="address test space dir ") as tmp_dir:
            tmp_dict_path = Path(tmp_dir) / "dict with spaces.json"
            shutil.copyfile(FIXTURE_PATH, tmp_dict_path)
            cmd = [
                sys.executable,
                "-m",
                "address_segmentation.io",
                "--dictionary",
                str(tmp_dict_path),
            ]
            proc = subprocess.run(
                cmd,
                input=json.dumps("Đường 3/2, Phường 1") + "\n",
                capture_output=True,
                text=True,
                env=get_cli_env(),
                check=False,
            )
            self.assertEqual(proc.returncode, 0, f"CLI stderr: {proc.stderr}")
            res = json.loads(proc.stdout.strip())
            self.assertEqual(res["street"], "3/2")
            self.assertEqual(res["ward"], "Phường 1")


if __name__ == "__main__":
    unittest.main()
