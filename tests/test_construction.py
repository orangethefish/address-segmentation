"""Construction optimizations must preserve Unicode and collision semantics."""

import unicodedata
import unittest

from address_segmentation.catalog import Catalog, validate_field_sequence
from address_segmentation.normalize import tokenize, tokenize_label
from address_segmentation.typing import TypingIndex


class ConstructionTests(unittest.TestCase):
    def test_label_normalization_agrees_with_source_tokens(self):
        cache = {}
        self.assertEqual(tokenize_label("3/2 53/2 A/B"),
                         (("3/2", "53/2", "a/b"), ("3/2", "53/2", "a/b")))
        self.assertEqual(tokenize_label("\u0301___"), ((), ()))
        labels = [
            "Đường Lê Lợi", unicodedata.normalize("NFD", "Đường Lê Lợi"),
            "3/2 53/2 A/B", "a__b / c//d", "\u0301___",
            "Straße İSTANBUL", "किरण a\u20dd", "x\u0301/y\u0301",
        ]
        for label in labels:
            with self.subTest(label=label):
                tokens = tokenize(label)
                expected = (tuple(t.nfc for t in tokens), tuple(t.folded for t in tokens))
                self.assertEqual(tokenize_label(label), expected)
                self.assertEqual(tokenize_label(label, token_cache=cache), expected)
                for token in tokens:
                    self.assertEqual(label[token.start:token.end], token.text)

    def test_validation_keeps_duplicates_and_rejects_invalid_later_items(self):
        self.assertEqual(validate_field_sequence(["Lê Lợi", "Lê Lợi"], "streets"),
                         ["Lê Lợi", "Lê Lợi"])
        for invalid in ("___", "\u0301", 42, " "):
            with self.subTest(invalid=invalid), self.assertRaises((TypeError, ValueError)):
                Catalog(streets=["Lê Lợi", "Lê Lợi", invalid])

    def test_cached_words_keep_canonical_collisions_and_field_identity(self):
        labels = ["Hòa Bình", "Hóa Bình", unicodedata.normalize("NFD", "Hòa Bình")]
        catalog = Catalog(streets=labels + labels, wards=["Phường Hòa Bình"],
                          provinces=["Hòa Bình"])
        self.assertEqual(len(catalog.entries), 5)
        self.assertEqual(len(catalog.lookup_folded(("hoa", "binh"))), 4)
        self.assertEqual(len(catalog.lookup_exact(("hòa", "bình"))), 3)
        permuted = Catalog(streets=list(reversed(labels)), wards=["Phường Hòa Bình"],
                           provinces=["Hòa Bình"])
        self.assertEqual(catalog.entries, permuted.entries)

    def test_typing_forms_retain_full_body_collisions_and_numeric_exclusion(self):
        catalog = Catalog(streets=["Lê Lợi", "Le Loi", "3/2"],
                          wards=["Phường Lê Lợi"])
        index = TypingIndex(catalog)
        self.assertEqual(len(index.words[("le", "loi")]), 3)
        self.assertEqual(len(index.compact["leloi"]), 3)
        self.assertEqual(len(index.words[("phuong", "le", "loi")]), 1)
        self.assertNotIn("3/2", index.compact)


if __name__ == "__main__":
    unittest.main()
