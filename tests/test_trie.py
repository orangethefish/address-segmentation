"""Behavior, completeness, ambiguity and prepared-runtime checks for trie + DP."""

from dataclasses import asdict, replace
import importlib.util
import itertools
import json
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
import unicodedata

from address_segmentation import AddressMatcher
from address_segmentation.catalog import Catalog
from address_segmentation.model import Candidate, MatcherConfig
from address_segmentation.prepared import PreparedMatcher
from address_segmentation.select import select_candidates, select_candidates_dp
from address_segmentation.trie import TrieApproximateIndex, TrieCatalog, search_trie
from address_segmentation.verify import thresholded_osa_distance
from tools.compile_dictionary import compile_dictionary
from tools.datasets import load_cases
from tools.prepare_dataset import prepare_dataset

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
TRIE_CONFIG = MatcherConfig(backend="trie_dp")


class TrieLookupTests(unittest.TestCase):
    def test_configuration_rejects_invalid_backends_and_node_limits(self):
        for value in (None, True, 1):
            with self.subTest(value=value), self.assertRaises(TypeError):
                MatcherConfig(backend=value)
        with self.assertRaises(ValueError):
            MatcherConfig(backend="unknown")
        for value in (True, "1", 0, -1):
            with self.subTest(value=value), self.assertRaises((TypeError, ValueError)):
                MatcherConfig(max_trie_nodes=value)

    def test_catalog_preserves_canonical_ids_aliases_and_collisions(self):
        data = dict(streets=["Hòa Bình", "Hóa Bình", "3/2", "Hòa Bình",
                             unicodedata.normalize("NFD", "Hòa Bình")],
                    wards=["Phường Hòa Bình", "Xã Hòa Bình"],
                    provinces=["Hòa Bình", "Hà Nội", "Hải Nam"])
        ordinary, trie = Catalog(**data), TrieCatalog(**data)
        self.assertEqual(trie.entries, ordinary.entries)
        for entry in ordinary.entries:
            for method, words in (("lookup_exact", entry.nfc_tokens),
                                  ("lookup_folded", entry.folded_tokens),
                                  ("lookup_body_exact", entry.body_nfc_tokens),
                                  ("lookup_body_folded", entry.body_folded_tokens)):
                self.assertEqual(getattr(trie, method)(words), getattr(ordinary, method)(words))
            self.assertEqual(trie.window_lengths(entry.folded_tokens[0]),
                             ordinary.window_lengths(entry.folded_tokens[0]))
        self.assertEqual(trie.lookup_province_initials("hn"), ordinary.lookup_province_initials("hn"))
        self.assertEqual(trie.max_tokens, ordinary.max_tokens)
        self.assertEqual(trie.token_lengths, ordinary.token_lengths)
        self.assertEqual(TrieCatalog().entries, ())
        self.assertEqual(TrieCatalog().lookup_exact(("absent",)), ())

    def test_trie_neighbors_match_complete_reference_search(self):
        words = ["".join(chars) for size in (5, 6)
                 for chars in itertools.product("ab", repeat=size)]
        index = TrieApproximateIndex(TrieCatalog(streets=words))
        queries = ["".join(chars) for size in range(3, 8)
                   for chars in itertools.product("ab", repeat=size)]
        for query in queries:
            for limit in range(3):
                with self.subTest(query=query, limit=limit):
                    matches, visited, _, exhausted = search_trie(index, query, limit)
                    actual = {form.form_id: distance for form, distance in matches}
                    expected = {form.form_id: distance for form in index._forms
                                if (distance := thresholded_osa_distance(
                                    form.folded_text, query, limit)) <= limit}
                    self.assertEqual(actual, expected)
                    self.assertIsNone(exhausted)
                    self.assertLessEqual(visited, 12000)

    def test_transpositions_unicode_and_field_filters(self):
        index = TrieApproximateIndex(TrieCatalog(streets=["abcde", "Hòa Bình", "Hóa Binh"],
                                                wards=["Phường abcde", "Xã abcde"]))
        matches, _, _, exhausted = search_trie(index, "abced", 1, field_hint="ward", marker_word="xa")
        self.assertIsNone(exhausted)
        self.assertEqual([(form.entry.canonical_label, distance) for form, distance in matches],
                         [("Xã abcde", 1)])
        matches, _, _, _ = search_trie(index, "hoa binh", 0)
        self.assertEqual({form.entry.canonical_label for form, _ in matches}, {"Hòa Bình", "Hóa Binh"})

    def test_rejected_terminal_collisions_still_consume_verification_budget(self):
        labels = ["Phường " + "".join(chars) for chars in itertools.product(
            *(tuple((char, char.upper())) for char in "abcdefgh"))]
        index = TrieApproximateIndex(TrieCatalog(wards=labels))
        matches, _, checked, exhausted = search_trie(
            index, "abdcefgh", 1, field_hint="ward", marker_word="xa", max_terminals=3)
        self.assertEqual(matches, [])
        self.assertEqual(checked, 3)
        self.assertEqual(exhausted, "verifications")


class SelectionDPTests(unittest.TestCase):
    def test_dp_matches_exhaustive_selection_including_null_margins(self):
        catalog = Catalog(streets=["Alpha", "Beta"], wards=["Alpha", "Beta"],
                          provinces=["Alpha", "Beta"])
        rng = random.Random(731)
        for trial in range(200):
            candidates = []
            for entry in catalog.entries:
                for _ in range(rng.randrange(3)):
                    start = rng.randrange(15)
                    candidates.append(Candidate(entry, start, start + rng.randrange(6), "exact",
                                                score=rng.randrange(-2, 10) * 5.0))
            config = MatcherConfig(top_k=64, min_score=rng.choice((-10, 0, 10)),
                                   ambiguity_margin=rng.choice((0, 0.5, 10, 100)))
            with self.subTest(trial=trial):
                self.assertEqual(select_candidates_dp(candidates, config),
                                 select_candidates(candidates, config))

    def test_repeated_evidence_does_not_require_top_k_abstention(self):
        data = dict(streets=["Lê Lợi"], provinces=["Hà Nội"])
        address = "Lê Lợi, " * 12 + "Hà Nội"
        self.assertIsNone(AddressMatcher(**data).identify(address)["street"])
        result, trace = AddressMatcher(**data, config=replace(TRIE_CONFIG, top_k=1)).identify_with_trace(address)
        self.assertEqual(result, dict(street="Lê Lợi", ward=None, province="Hà Nội"))
        self.assertEqual(trace["selection"]["pruned_fields"], [])


class TrieMatcherTests(unittest.TestCase):
    def test_all_control_fixtures_and_trace_parity(self):
        for prefix in ("", "recovery-", "typing-"):
            data = json.loads((FIXTURES / (prefix + "dictionary.json")).read_text())
            matcher = AddressMatcher(**data, config=TRIE_CONFIG)
            for case in load_cases(FIXTURES / (prefix + "cases.jsonl"), data):
                with self.subTest(fixture=prefix, case=case.id):
                    result, trace = matcher.identify_with_trace(case.address)
                    self.assertEqual(result, case.expected)
                    self.assertEqual(matcher.identify(case.address), result)
                    for name, count in trace["budgets"]["counts"].items():
                        self.assertLessEqual(count, trace["budgets"]["limits"][name])

    def test_reviewed_user_dataset_and_original_ambiguities(self):
        source = ROOT / "testcases" / "1"
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            prepare_dataset(source, output, source / "review.json")
            data = json.loads((output / "dictionary.json").read_text())
            matcher = AddressMatcher(**data, config=TRIE_CONFIG)
            for case in load_cases(output / "cases.jsonl", data):
                with self.subTest(case=case.id):
                    self.assertEqual(matcher.identify(case.address), case.expected)
            original = load_cases(output / "raw-cases.jsonl", data)
            self.assertEqual(sum(matcher.identify(case.address) == case.expected for case in original), 994)

    def test_budget_exhaustion_preserves_exact_province_and_rejects_prefix(self):
        data = dict(streets=["An Phú", "An Phú Đông"], provinces=["Hà Nội"])
        matcher = AddressMatcher(**data, config=replace(TRIE_CONFIG, max_trie_nodes=1))
        result, trace = matcher.identify_with_trace("Đường An Phú Dnog, Hà Nội")
        self.assertEqual(result, dict(street=None, ward=None, province="Hà Nội"))
        self.assertTrue(trace["budgets"]["exhaustion_flags"]["trie_nodes"])
        self.assertEqual(trace["budgets"]["counts"]["trie_nodes"], 1)
        for config in (replace(TRIE_CONFIG, max_verifications=1),
                       replace(TRIE_CONFIG, max_candidates_per_span=1)):
            matcher = AddressMatcher(streets=["Hòa Bình", "Hóa Binh"], provinces=["Hà Nội"], config=config)
            result, trace = matcher.identify_with_trace("Đường Hoa Bihn, Hà Nội")
            self.assertEqual(result, dict(street=None, ward=None, province="Hà Nội"))
            self.assertTrue(trace["budgets"]["exhausted"])
            for name, count in trace["budgets"]["counts"].items():
                self.assertLessEqual(count, trace["budgets"]["limits"][name])

    def test_marker_boundaries_accents_numeric_names_and_order(self):
        matcher = AddressMatcher(
            streets=["Nguyễn Thị Minh Khai", "Minh Khai", "Nguyễn Huệ", "3/2", "Đường số 123"],
            wards=["Phường Minh Khai", "Xã Minh Khai", "Phường Vĩnh Nguyên"],
            provinces=["Huế"], config=TRIE_CONFIG)
        cases = [
            ("Nguyen Thi Minh Khai", dict(street="Nguyễn Thị Minh Khai", ward=None, province=None)),
            ("Phuong Vinh Nguyen, Hue", dict(street=None, ward="Phường Vĩnh Nguyên", province="Huế")),
            ("Hue, X.MinhKhai, Đường 3/2", dict(street="3/2", ward="Xã Minh Khai", province="Huế")),
            ("Đường 3/3, Hue", dict(street=None, ward=None, province="Huế")),
            ("Số 123", dict(street=None, ward=None, province=None)),
        ]
        for address, expected in cases:
            for text in (address, unicodedata.normalize("NFD", address)):
                with self.subTest(address=text):
                    self.assertEqual(matcher.identify(text), expected)


class PreparedTrieTests(unittest.TestCase):
    def test_prepared_and_standalone_traces_match_source_for_all_paths(self):
        with tempfile.TemporaryDirectory(prefix="trie prepared ") as directory:
            root = Path(directory)
            for prefix, config in [(p, TRIE_CONFIG) for p in ("", "recovery-", "typing-")] + [
                ("recovery-", replace(TRIE_CONFIG, max_trie_nodes=1)),
                ("recovery-", replace(TRIE_CONFIG, max_verifications=1)),
                ("recovery-", replace(TRIE_CONFIG, max_candidates_per_span=1)),
                ("typing-", replace(TRIE_CONFIG, enable_fuzzy=False)),
                ("typing-", replace(TRIE_CONFIG, enable_typing=False)),
                ("typing-", replace(TRIE_CONFIG, enable_fuzzy=False, enable_typing=False,
                                     enable_compact=False)),
                ("recovery-", replace(TRIE_CONFIG, enable_compact=False, enable_typing=False)),
            ]:
                config_path = root / "config.json"
                config_path.write_text(json.dumps(asdict(config)))
                bundle = root / "bundle"
                compile_dictionary(FIXTURES / (prefix + "dictionary.json"), bundle, str(config_path))
                spec = importlib.util.spec_from_file_location("trie_test_runtime", bundle / "matcher_runtime.py")
                runtime = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(runtime)
                data = json.loads((FIXTURES / (prefix + "dictionary.json")).read_text())
                matcher = AddressMatcher(**data, config=config)
                with PreparedMatcher(bundle / "dictionary.idx") as prepared, runtime.PreparedMatcher(bundle / "dictionary.idx") as slim:
                    self.assertEqual(prepared._store._entries, {})
                    for case in load_cases(FIXTURES / (prefix + "cases.jsonl"), data):
                        with self.subTest(fixture=prefix, config=config, case=case.id):
                            expected = matcher.identify_with_trace(case.address)
                            self.assertEqual(prepared.identify_with_trace(case.address), expected)
                            self.assertEqual(slim.identify_with_trace(case.address), expected)
                shutil.rmtree(bundle / "__pycache__")
                address = "Đường Le Loj, Phường 1"
                process = subprocess.run([sys.executable, "-S", "-B", str(bundle / "submission.py")],
                                         input=json.dumps(address) + "\n", text=True,
                                         capture_output=True, cwd=directory, timeout=10)
                self.assertEqual(process.returncode, 0, process.stderr)
                self.assertEqual(json.loads(process.stdout), matcher.identify(address))


if __name__ == "__main__":
    unittest.main()
