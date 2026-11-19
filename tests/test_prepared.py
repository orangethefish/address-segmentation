"""Dictionary-only compilation must preserve the complete matching pipeline."""

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from address_segmentation import AddressMatcher
from address_segmentation.io import load_dictionary
from address_segmentation.model import MatcherConfig
from address_segmentation.prepared import (
    MappedDictionary, PreparedMatcher, decode_address_line, encode_result,
)
from tools.compile_dictionary import compile_dictionary, write_index
from tools.benchmark_prepared import benchmark_prepared
from tools.datasets import load_cases

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"


def load_runtime(path):
    spec = importlib.util.spec_from_file_location("prepared_test_runtime", path / "matcher_runtime.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PreparedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="prepared tests ")
        cls.base = Path(cls.temporary.name)
        cls.bundles = {}
        for name in ("", "recovery-", "typing-"):
            destination = cls.base / (name or "foundation")
            compile_dictionary(FIXTURES / (name + "dictionary.json"), destination)
            cls.bundles[name] = destination

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_source_and_standalone_match_predictions_traces_and_budgets(self):
        for name, destination in self.bundles.items():
            data = load_dictionary(str(FIXTURES / (name + "dictionary.json")))
            cases = load_cases(FIXTURES / (name + "cases.jsonl"), data)
            ordinary = AddressMatcher(**data)
            standalone = load_runtime(destination)
            with PreparedMatcher(destination / "dictionary.idx") as prepared, standalone.PreparedMatcher(destination / "dictionary.idx") as slim:
                for case in cases:
                    with self.subTest(fixture=name, case=case.id):
                        expected = ordinary.identify_with_trace(case.address)
                        self.assertEqual(prepared.identify_with_trace(case.address), expected)
                        self.assertEqual(slim.identify_with_trace(case.address), expected)
                        self.assertEqual(slim.identify(case.address), expected[0])

    def test_configuration_and_budget_exhaustion_remain_identical(self):
        data = load_dictionary(str(FIXTURES / "recovery-dictionary.json"))
        cases = load_cases(FIXTURES / "recovery-cases.jsonl", data)
        configurations = [MatcherConfig(max_postings=1, max_verifications=1),
                          MatcherConfig(enable_fuzzy=False),
                          MatcherConfig(enable_compact=False, enable_typing=False),
                          MatcherConfig(enable_fuzzy=False, enable_compact=False, enable_typing=False)]
        for index, config in enumerate(configurations):
            config_path = self.base / "config.json"
            config_path.write_text(json.dumps({f: getattr(config, f) for f in config.__dataclass_fields__}))
            destination = self.base / f"configuration-{index}"
            compile_dictionary(FIXTURES / "recovery-dictionary.json", destination, str(config_path))
            ordinary = AddressMatcher(**data, config=config)
            with load_runtime(destination).PreparedMatcher(destination / "dictionary.idx") as slim:
                for case in cases:
                    self.assertEqual(slim.identify_with_trace(case.address), ordinary.identify_with_trace(case.address))

    def test_protocol_codec_matches_standard_json(self):
        for value in ("", "Lê Lợi", 'quote " slash / backslash \\', "x\n\t\b\f\r", "😀", "\ud800"):
            for ascii_only in (True, False):
                line = " \t" + json.dumps(value, ensure_ascii=ascii_only) + "\r\n"
                self.assertEqual(decode_address_line(line), json.loads(line))
        for invalid in ('"abc"{}', '"abc" "def"', '"abc"\u00a0', '\u00a0"abc"',
                        '"bad\\q"', '"unterminated', '"raw\x01control"', '', 'null', '42', '{}', '[]'):
            with self.subTest(invalid=invalid), self.assertRaises((TypeError, ValueError)):
                decode_address_line(invalid)
        value = {"street": 'Lê "Lợi" / \\', "ward": "😀\n", "province": None}
        self.assertEqual(json.loads(encode_result(value)), value)

    def test_immutable_generated_models_and_replacement_validate(self):
        runtime = load_runtime(self.bundles[""])
        config = runtime.MatcherConfig()
        with self.assertRaises(AttributeError):
            config.top_k = 1
        with self.assertRaises(AttributeError):
            del config.top_k
        changed = runtime.replace(config, max_postings=1)
        self.assertEqual(changed.max_postings, 1)
        self.assertNotEqual(config, changed)
        self.assertEqual(hash(config), hash(runtime.MatcherConfig()))
        with self.assertRaises(ValueError):
            runtime.replace(config, max_postings=0)
        with self.assertRaises(TypeError):
            runtime.replace(config, unknown=1)

    def test_form_metadata_is_lazy_and_shared_across_postings(self):
        store = MappedDictionary(self.bundles["recovery-"] / "dictionary.idx")
        try:
            form = store.form(0)
            self.assertEqual(store._entries, {})
            self.assertGreater(form.length, 0)
            self.assertEqual(store._entries, {})
            self.assertIs(form, store.form(0))
            self.assertEqual({form: 2}[store.form(0)], 2)
            self.assertEqual(form.entry, store.entry(form.entry.entry_id))
            self.assertEqual(len(store._entries), 1)
        finally:
            store.close()

    def test_runtime_pairing_and_corrupt_index_fail(self):
        bundle = self.bundles[""]
        runtime = load_runtime(bundle)
        runtime.RUNTIME_ID = "wrong-runtime"
        with self.assertRaisesRegex(ValueError, "runtime mismatch"):
            runtime.PreparedMatcher(bundle / "dictionary.idx")
        for invalid in (b"", b"ASIDX002", b"ASIDX002\xff\xff\xff\xff"):
            path = self.base / "corrupt.idx"
            path.write_bytes(invalid)
            with self.assertRaises((ValueError, EOFError)):
                MappedDictionary(path)

    def test_full_hash_collisions_never_overwrite_keys(self):
        # Known CRC32 collision; correctness must compare complete keys as well.
        import zlib
        from address_segmentation.prepared import index_key
        collision = ("12433751591494333801", "12249025883842003649")
        self.assertEqual(zlib.crc32(index_key("test", collision[0])),
                         zlib.crc32(index_key("test", collision[1])))
        source = MappedDictionary(self.bundles[""] / "dictionary.idx")
        metadata = dict(source.metadata)
        source.close()
        path = self.base / "collisions.idx"
        write_index(path, metadata, [("test", collision[0], "first"),
                                     ("test", collision[1], "second")])
        store = MappedDictionary(path)
        try:
            self.assertEqual(store.get("test", collision[0]), "first")
            self.assertEqual(store.get("test", collision[1]), "second")
            self.assertIsNone(store.get("test", "absent"))
        finally:
            store.close()

    def test_repeated_build_is_deterministic_and_reads_only_dictionary(self):
        first = compile_dictionary(FIXTURES / "dictionary.json", self.base / "repeat-one")
        second = compile_dictionary(FIXTURES / "dictionary.json", self.base / "repeat-two")
        self.assertEqual(first["files"], second["files"])
        self.assertEqual(first["metadata"], second["metadata"])

    def test_compiled_hierarchy_resolves_only_text_supported_ambiguity(self):
        data = {"streets": ["Lê Lợi", "Trần Phú"], "wards": ["Hòa Bình", "Tân Bình"],
                "provinces": ["Hà Nội"]}
        dictionary = self.base / "hierarchy-dictionary.json"
        dictionary.write_text(json.dumps(data), encoding="utf-8")
        hierarchy = self.base / "hierarchy.json"
        hierarchy.write_text(json.dumps({"Hà Nội": {"Hòa Bình": [], "Tân Bình": ["Lê Lợi"]}}),
                             encoding="utf-8")
        destination = self.base / "with-hierarchy"
        manifest = compile_dictionary(dictionary, destination, hierarchy_path=hierarchy)
        self.assertIn("hierarchy_hash", manifest["metadata"])
        ordinary = AddressMatcher(**data)
        address = "Đường Lê Lợi, Phường Hòa Bình, Phường Tân Bình, Thành phố Hà Nội"
        self.assertIsNone(ordinary.identify(address)["ward"])
        hierarchy.unlink()
        with load_runtime(destination).PreparedMatcher(destination / "dictionary.idx") as prepared:
            self.assertEqual(prepared.identify(address), {
                "street": "Lê Lợi", "ward": "Tân Bình", "province": "Hà Nội",
            })
            for independent in ("Đường Lê Lợi", "Đường Trần Phú, Phường Hòa Bình, Hà Nội",
                                "Đường Lê Lợi, Phường Hòa Bình, Hà Nội"):
                self.assertEqual(prepared.identify(independent), ordinary.identify(independent))

    def test_hierarchy_rejects_street_absent_from_dictionary(self):
        hierarchy = self.base / "invalid-hierarchy.json"
        hierarchy.write_text(json.dumps({
            "Thành phố Hồ Chí Minh": {"Phường Bến Thành": ["unknown street"]},
        }), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Unknown or duplicate street"):
            compile_dictionary(FIXTURES / "dictionary.json", self.base / "invalid-hierarchy",
                               hierarchy_path=hierarchy)

    def test_bundle_runs_without_repository_with_or_without_bytecode(self):
        bundle = self.bundles[""]
        destination = self.base / "relocated bundle"
        shutil.copytree(bundle, destination, dirs_exist_ok=True)
        command = [sys.executable, "-S", "-B", str(destination / "submission.py")]
        address = "Đường 3/2, Phường 1"
        expected = AddressMatcher(**load_dictionary(str(FIXTURES / "dictionary.json"))).identify(address)
        for has_bytecode in (True, False):
            if not has_bytecode:
                shutil.rmtree(destination / "__pycache__")
            proc = subprocess.run(command, input=json.dumps(address) + "\n", text=True,
                                  capture_output=True, cwd=self.base, check=False)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(proc.stdout), expected)
            if not has_bytecode:
                self.assertFalse((destination / "__pycache__").exists())
        for invalid in ("\n", '"a"{}\n', '{}\n'):
            proc = subprocess.run(command, input=invalid, text=True, capture_output=True, cwd=self.base)
            self.assertNotEqual(proc.returncode, 0)
            self.assertEqual(proc.stdout, "")

    def test_prepared_benchmark_scope_and_bundle_identity(self):
        report = benchmark_prepared(
            self.bundles[""], FIXTURES / "dictionary.json", FIXTURES / "cases.jsonl",
            processes=2, cold_cases=1, no_site=True, source_only=True)
        self.assertEqual(report["phases"]["cold_process_request"]["count"], 2)
        self.assertEqual(report["cold_coverage"]["matches_expected"], 2)
        self.assertFalse(report["cold_coverage"]["full_dataset"])
        self.assertEqual(report["cold_coverage"]["warmup"], 0)
        self.assertEqual(report["thresholds"]["scope"], "cold_process_request")
        stats = report["phases"]["cold_process_request"]
        self.assertEqual(report["thresholds"]["mean_target_met"], stats["mean_ms"] <= 10)
        self.assertEqual(report["thresholds"]["max_target_met"], stats["max_ms"] <= 100)
        self.assertEqual(report["measurement_contract"]["command"][1:3], ["-S", "-B"])
        self.assertEqual([r["round"] for r in report["raw_cold_process_timings"]], [0, 1])
        data = load_dictionary(str(FIXTURES / "dictionary.json"))
        data["streets"].append("Different Dictionary")
        different = self.base / "different.json"
        different.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "does not match"):
            benchmark_prepared(self.bundles[""], different, FIXTURES / "cases.jsonl")


if __name__ == "__main__":
    unittest.main()
