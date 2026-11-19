import ast
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unicodedata
import unittest

from address_segmentation import AddressMatcher
from address_segmentation.model import MatcherConfig
from address_segmentation.normalize import tokenize
from tools.build_submission import TEMPLATE, build_submission, code
from tools.evaluate_teacher_v2 import compare_teacher_v2, load_teacher_v2, score_teacher_v2


def result(street=None, ward=None, province=None):
    return dict(street=street, ward=ward, province=province)


DICTIONARY = {
    "streets": ["Đường 3/2", "3/2", "Đường Số 8", "Đường Lê Duẩn", "Lê Duẩn"],
    "wards": ["Phường An Phú", "Phường Minh An", "Xã Tân Lợi", "Thị trấn Cửa Tùng", "Bình", "PBình"],
    "provinces": ["Thành phố Huế", "Tỉnh Lào Cai"],
}


class JoinedMarkerTests(unittest.TestCase):
    backend = "indexed"

    def setUp(self):
        self.config = MatcherConfig(backend=self.backend)
        self.matcher = AddressMatcher(**DICTIONARY, config=self.config)

    def test_joined_and_separated_use_dictionary_names_and_types(self):
        fixtures = {
            "Đường. 3/2, Thị trấn Cửa Tùng, Thành phố Huế": result("Đường 3/2", "Thị trấn Cửa Tùng", "Thành phố Huế"),
            "ĐƯỜNG3/2, THI TRANCUA TUNG, TPHUẾ": result("Đường 3/2", "Thị trấn Cửa Tùng", "Thành phố Huế"),
            "74/49/3 ĐườngSố 8 xTân Lợi TỉnhLào Cai": result("Đường Số 8", "Xã Tân Lợi", "Tỉnh Lào Cai"),
            "PAn Phú TP.Huế": result(ward="Phường An Phú", province="Thành phố Huế"),
            "pAn Phú tinhlao cai": result(ward="Phường An Phú", province="Tỉnh Lào Cai"),
            "XAn Phú, TP Lào Cai": result(),
            "XMinh Ahn, TPLào Cai": result(),
            "Phường,Tân Lợi": result(ward="Xã Tân Lợi"),
            "Thị; trấnCửa Tùng": result(),
            "Thị\ntrấnCửa Tùng": result(),
            "74/53/2": result(),
            "53/2": result(),
            "Số 22 ĐườngSố 8 12": result(),
        }
        for address, expected in fixtures.items():
            with self.subTest(address=address):
                self.assertEqual(self.matcher.identify(address), expected)

    def test_literal_label_and_ambiguity_are_preserved(self):
        self.assertEqual(self.matcher.identify("PBình"), result())
        self.assertEqual(self.matcher.identify("P. PBình"), result(ward="PBình"))
        self.assertEqual(self.matcher.identify("Paris Txyz Xyz"), result())
        cross_field = AddressMatcher(streets=["PMinh An"], wards=["Minh An"], config=self.config)
        self.assertEqual(cross_field.identify("PMinh An"), result())
        self.assertEqual(cross_field.identify("Đường PMinh An"), result(street="PMinh An"))

    def test_typing_compact_and_fuzzy_use_only_the_body(self):
        fixtures = {
            "phườngAnPhú": ("Phường An Phú", "compact"),
            "PhườngAn Phus": ("Phường An Phú", "telex"),
            "PhườngAn Phu1": ("Phường An Phú", "vni"),
            "PhườngMinh Ahn": ("Phường Minh An", "fuzzy"),
            "PMinh Ahn": ("Phường Minh An", "fuzzy"),
        }
        for address, (label, path) in fixtures.items():
            with self.subTest(address=address):
                actual, trace = self.matcher.identify_with_trace(address)
                self.assertEqual(actual, result(ward=label))
                winner = next(item for item in trace["retained"] if item["label"] == label)
                self.assertEqual(winner["path"], path)
                self.assertEqual(winner["span"], [0, len(address)])
                self.assertEqual(winner["cost"], 1)
        self.assertEqual(self.matcher.identify("PhườngAn Phuf"), result())
        self.assertEqual(
            self.matcher.identify("PhườngMinh Ahn Thành phốHuế"),
            result(ward="Phường Minh An", province="Thành phố Huế"),
        )

    def test_original_offsets_including_decomposed_markers(self):
        joined = unicodedata.normalize("NFD", "Thị trấnCửa Tùng")
        address = f"74/53/2, ({joined}), TỉnhLào Cai"
        actual, trace = self.matcher.identify_with_trace(address)
        self.assertEqual(actual, result(ward="Thị trấn Cửa Tùng", province="Tỉnh Lào Cai"))
        candidate = next(item for item in trace["retained"] if item["field"] == "ward")
        start, end = candidate["span"]
        self.assertEqual(address[start:end], joined)
        self.assertEqual(candidate["cost"], 0)
        for pad in ("a " * 160, "ấ " * 160):
            text = pad + unicodedata.normalize("NFD", "xóm_74/3/2")
            self.assertEqual([(t.text, t.start, t.end) for t in tokenize(text)[-2:]],
                             [(text[len(pad):-7], len(pad), len(text)-7),
                              ("74/3/2", len(text)-6, len(text))])


    def test_closer_joined_split_cannot_bypass_marker_type(self):
        matcher = AddressMatcher(wards=["Phường Minh Khai"], provinces=["Hà Nội"], config=self.config)
        self.assertEqual(matcher.identify("XMinh Kahai, Hà Nội"), result(province="Hà Nội"))
        self.assertEqual(matcher.identify("PMinh Kahai, Hà Nội"),
                         result(ward="Phường Minh Khai", province="Hà Nội"))


class TrieJoinedMarkerTests(JoinedMarkerTests):
    backend = "trie_dp"


class TeacherDataTests(unittest.TestCase):
    def test_exact_labels_duplicate_addresses_and_zero_on_exception(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for field, label in (("province", "Tỉnh Lào Cai"), ("ward", "Xã Tân Lợi"), ("street", "Lê Duẩn")):
                (root / f"public_list_{field}.txt").write_text(f"\n{label}\n", encoding="utf-8")
            path = root / "public.json"
            row = {"address": "x", "result": result("Lê Duẩn", "Xã Tân Lợi", "Tỉnh Lào Cai")}
            path.write_text(json.dumps([row]), encoding="utf-8")
            lists, cases, metadata = load_teacher_v2(root)
            self.assertEqual(lists["ward"], ["Xã Tân Lợi"])
            self.assertTrue(metadata["official_data"])
            self.assertEqual(len(metadata["source_hashes"]), 4)
            missed = score_teacher_v2(cases, {}, {"1": "RuntimeError: mock failure"})
            scored = score_teacher_v2(cases, {"1": row["result"]}, {})
            self.assertEqual(missed["correct_fields"], 0)
            self.assertEqual(missed["exception_count"], 1)
            self.assertEqual(scored["score_10"], 10)
            change = compare_teacher_v2(cases, metadata, scored, {"metadata": metadata, "metrics": missed})
            self.assertEqual(change["complete_address_gains"], ["1"])
            self.assertEqual(change["regressions"], [])
            for duplicate, error in ((row, "Duplicate"), ({**row, "result": result()}, "Conflicting")):
                path.write_text(json.dumps([row, duplicate]), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, error):
                    load_teacher_v2(root)
            path.write_text(json.dumps([{**row, "result": result(street="le duan")}]), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "missing from dictionary"):
                load_teacher_v2(root)


class SubmissionTests(unittest.TestCase):
    def test_build_structure_and_clean_installation(self):
        for backend in ("indexed", "trie_dp"):
            with self.subTest(backend=backend):
                self.check_build_structure_and_clean_installation(backend)

    def check_build_structure_and_clean_installation(self, backend):
        with TemporaryDirectory() as directory:
            path = build_submission("Group_72", directory, backend=backend)
            notebook_path = path.with_suffix(".ipynb")
            notebook_bytes = notebook_path.read_bytes()
            python_bytes = path.read_bytes()
            build_submission("Group_72", directory, backend=backend)
            self.assertEqual(notebook_path.read_bytes(), notebook_bytes)
            self.assertEqual(path.read_bytes(), python_bytes)
            book = json.loads(notebook_bytes)
            template = json.loads(TEMPLATE.read_bytes())
            self.assertEqual([c["source"] for c in book["cells"][-2:]],
                             [c["source"] for c in template["cells"][-2:]])
            for cell in book["cells"]:
                self.assertEqual(cell["outputs"], [])
                self.assertIsNone(cell["execution_count"])
            student = ast.parse("\n".join(code(cell) for cell in book["cells"][:5]))
            self.assertEqual([node.name for node in student.body if isinstance(node, ast.ClassDef)], ["Solution"])
            self.assertFalse(any(isinstance(node, ast.FunctionDef) for node in student.body))
            import_nodes = ast.parse(code(book["cells"][3])).body
            self.assertTrue(all(isinstance(node, (ast.Import, ast.ImportFrom)) for node in import_nodes))
            self.assertNotIn("address_segmentation", code(book["cells"][3]))
            self.assertNotIn("public.json", code(book["cells"][4]))
            self.assertNotIn("pip", code(book["cells"][2]))

            cases = ["", "PBình", "PMinh Ahn", "PhườngAn Phu1", "Đường3/2, Thị trấnCửa Tùng, TPHuế",
                     "ĐườngLê Duẩn, XAn Phú, TPLào Cai", "74/53/2"]
            data_path = TEMPLATE.parent / "v2_2_level_address"
            inventories, public_cases, _ = load_teacher_v2(data_path)
            test_data = []
            config = MatcherConfig(backend=backend)
            public_matcher = AddressMatcher(**{field + "s": labels for field, labels in inventories.items()},
                                            config=config)
            self.assertEqual([public_matcher.identify(case.address) for case in public_cases],
                             [case.expected for case in public_cases])
            for dictionaries, addresses in (
                (DICTIONARY, cases),
                ({field + "s": labels for field, labels in inventories.items()},
                 [case.address for case in public_cases]),
            ):
                matcher = AddressMatcher(**dictionaries, config=config)
                params = [dictionaries[field] for field in ("provinces", "wards", "streets")]
                test_data.append((params, [(address, matcher.identify(address)) for address in addresses]))

            solution = next(node for node in student.body if isinstance(node, ast.ClassDef))
            constructor = next(node for node in solution.body
                               if isinstance(node, ast.FunctionDef) and node.name == "__init__")
            self.assertEqual([arg.arg for arg in constructor.args.args],
                             ["self", "list_province", "list_ward", "list_street"])
            driver = '''
import importlib.util, inspect, json, runpy, sys, types
from pathlib import Path
assert importlib.util.find_spec("address_segmentation") is None
path = Path(sys.argv[1])
py_solution = runpy.run_path(str(path))["Solution"]
book = json.loads(path.with_suffix(".ipynb").read_bytes())
module = types.ModuleType("isolated_notebook")
sys.modules[module.__name__] = module
for cell in book["cells"][:5]:
    exec("".join(cell["source"]), module.__dict__)
data = json.load(sys.stdin)
for exported in (py_solution, module.Solution):
    assert list(inspect.signature(exported).parameters) == ["list_province", "list_ward", "list_street"]
    for params, expected in data:
        snapshot = [labels[:] for labels in params]
        solution = exported(*params)
        assert solution._matcher.config.backend == sys.argv[2]
        reordered = exported(*(list(reversed(labels)) + labels[:1] for labels in params))
        empty = exported([], [], [])
        for address, answer in expected:
            result = solution.process(address)
            assert result == reordered.process(address) == answer, (address, result, answer)
            result["province_normalized"] = None
            assert solution.process(address) == answer
            assert empty.process(address) == dict(street=None, ward=None, province=None)
        assert params == snapshot
'''
            process = subprocess.run(
                [sys.executable, "-I", "-S", "-c", driver, str(path), backend], cwd=directory,
                input=json.dumps(test_data), text=True, capture_output=True, check=False,
            )
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
