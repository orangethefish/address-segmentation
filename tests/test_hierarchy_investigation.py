"""Controls for the relationship representation and isolated research bundle."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from tools.investigate_hierarchy import area_masks, build_prototype, unique_object


HIERARCHY = {
    "Tỉnh Nam": {"Xã Đông": ["Hòa An"], "Xã Tây": ["Hòa Bình"]},
    "Tỉnh Bắc": {"Xã Đông": ["Hòa Bình"], "Xã Tây": ["Hòa An"]},
}


class HierarchyInvestigationTests(unittest.TestCase):
    def test_shared_location_required_even_when_every_pair_is_valid(self):
        nodes, masks = area_masks(HIERARCHY)
        self.assertEqual(len(nodes), 4)
        s, w, p = (masks[key] for key in
                   (("street", "Hòa An"), ("ward", "Xã Đông"), ("province", "Tỉnh Bắc")))
        self.assertTrue(s & w)
        self.assertTrue(w & p)
        self.assertTrue(s & p)
        self.assertFalse(s & w & p)
        self.assertTrue(s & w & masks["province", "Tỉnh Nam"])

    def test_assignment_independent_of_input_order(self):
        reversed_hierarchy = {p: dict(reversed(list(wards.items())))
                              for p, wards in reversed(list(HIERARCHY.items()))}
        self.assertEqual(area_masks(HIERARCHY), area_masks(reversed_hierarchy))

    def test_empty_branches_preserve_partial_locations(self):
        nodes, masks = area_masks({"Empty": {}, "Province": {"Ward": []}})
        self.assertEqual(len(nodes), 2)
        self.assertTrue(masks["province", "Empty"])
        self.assertTrue(masks["province", "Province"] & masks["ward", "Ward"])
        self.assertFalse(masks["province", "Empty"] & masks["ward", "Ward"])
        self.assertEqual(area_masks({}), ([], {}))

    def test_duplicate_keys_and_invalid_shapes_rejected(self):
        for value in ('{"P": {}, "P": {}}', '{"P": {"W": [], "W": []}}'):
            with self.assertRaises(ValueError):
                json.loads(value, object_pairs_hook=unique_object)
        for value in ([], {"P": []}, {"P": {"W": "street"}},
                      {"P": {"W": [""]}}, {"P": {"W": ["S", "S"]}}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                area_masks(value)

    def test_prototype_selection_conflicts_and_absent_components(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dictionary = dict(streets=["Hòa An", "Hòa Bình"], wards=["Xã Đông", "Xã Tây"],
                              provinces=["Tỉnh Nam", "Tỉnh Bắc"])
            (root / "dictionary.json").write_text(json.dumps(dictionary), encoding="utf-8")
            (root / "hierarchy.json").write_text(json.dumps(HIERARCHY), encoding="utf-8")
            bundle = root / "bundle"
            build_prototype(root / "dictionary.json", root / "hierarchy.json", bundle)
            spec = importlib.util.spec_from_file_location("hierarchy_control", bundle / "matcher_runtime.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            with module.PreparedMatcher(bundle / "dictionary.idx") as matcher:
                self.assertEqual(matcher.identify("Hòa An, Xã Đông"),
                                 dict(street="Hòa An", ward="Xã Đông", province=None))
                entries = {(e.field, e.canonical_label): e for e in matcher.catalog.entries}
                candidates = [module.Candidate(entries[key], i * 20, i * 20 + 10, "exact", score=100)
                              for i, key in enumerate((("street", "Hòa An"), ("ward", "Xã Đông"),
                                                       ("province", "Tỉnh Bắc")))]
                result, _, _ = module.select_candidates(candidates, matcher.config, matcher._hierarchy)
                self.assertEqual(result, dict(street=None, ward=None, province=None))
                candidates[1] = module.replace(candidates[1], score=200)
                candidates[2] = module.replace(candidates[2], score=300)
                result, _, _ = module.select_candidates(candidates, matcher.config, matcher._hierarchy)
                self.assertEqual(result, dict(street=None, ward="Xã Đông", province="Tỉnh Bắc"))


if __name__ == "__main__":
    unittest.main()
