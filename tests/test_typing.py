"""Stage E controls, feature isolation, spans, collisions and bounded work."""
from dataclasses import replace
import json
from pathlib import Path
import unicodedata
import unittest

from address_segmentation import AddressMatcher
from address_segmentation.model import MatcherConfig
from tools.datasets import load_cases

FIXTURES = Path(__file__).resolve().parent / 'fixtures'


class TypingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dictionary = json.loads((FIXTURES / 'typing-dictionary.json').read_text())
        cls.cases = load_cases(FIXTURES / 'typing-cases.jsonl', cls.dictionary)

    def test_reviewed_controls(self):
        matcher = AddressMatcher(**self.dictionary)
        for case in self.cases:
            with self.subTest(case=case.id):
                result, trace = matcher.identify_with_trace(case.address)
                self.assertEqual(result, case.expected)
                self.assertEqual(result, matcher.identify(case.address))
                for stage in ('retrieved', 'verified', 'retained'):
                    for candidate in trace[stage]:
                        a, b = candidate['span']
                        self.assertTrue(0 <= a < b <= len(case.address))
                for name, limit in trace['budgets']['limits'].items():
                    self.assertLessEqual(trace['budgets']['counts'][name], limit)
                json.dumps(trace)

    def test_independent_switches(self):
        for flag, address, expected in [('enable_typing', 'cung4', 'cũng'),
                                         ('enable_compact', 'Le Lo i', 'Lê Lợi')]:
            config = MatcherConfig(enable_fuzzy=False, enable_typing=False, enable_compact=False)
            self.assertIsNone(AddressMatcher(**self.dictionary, config=config).identify(address)['street'])
            enabled = replace(config, **{flag: True})
            self.assertEqual(AddressMatcher(**self.dictionary, config=enabled).identify(address)['street'], expected)

    def test_literal_text_remains_a_competing_hypothesis(self):
        matcher = AddressMatcher(streets=['cũng', 'cungx', 'Ankl', 'An'])
        self.assertEqual(matcher.identify('cungx')['street'], 'cungx')
        self.assertEqual(matcher.identify('Ankl')['street'], 'Ankl')

    def test_tone_disambiguates_folded_collision(self):
        matcher = AddressMatcher(streets=['cũng', 'cùng'])
        self.assertIsNone(matcher.identify('cung')['street'])
        self.assertEqual(matcher.identify('cungx')['street'], 'cũng')
        self.assertEqual(matcher.identify('cung2')['street'], 'cùng')

    def test_permutation_nfd_and_duplicate_stability(self):
        first = AddressMatcher(**self.dictionary)
        second = AddressMatcher(**{k: list(reversed(v)) + v for k, v in self.dictionary.items()})
        for case in self.cases:
            self.assertEqual(first.identify(case.address), second.identify(case.address))
            self.assertEqual(first.identify(case.address), first.identify(unicodedata.normalize('NFD', case.address)))

    def test_typing_budget_abstains_and_preserves_exact_province(self):
        matcher = AddressMatcher(streets=['Lê Lợi', 'Lé Lọi'], provinces=['Hà Nội'],
            config=MatcherConfig(enable_fuzzy=False, max_postings=1))
        result, trace = matcher.identify_with_trace('Đường LeLoi, Hà Nội')
        self.assertIsNone(result['street'])
        self.assertEqual(result['province'], 'Hà Nội')
        self.assertTrue(trace['budgets']['exhausted'])

    def test_compact_does_not_erase_punctuation_or_digits(self):
        matcher = AddressMatcher(streets=['Lê Lợi', '3/2'],
            config=MatcherConfig(enable_fuzzy=False, enable_typing=False))
        for address in ('Le/Lo i', 'Le-Lo i', 'Le.Lo i', '3 2', '32'):
            self.assertIsNone(matcher.identify(address)['street'])

    def test_compact_preserves_accent_evidence(self):
        matcher = AddressMatcher(streets=['Tân An', 'Tấn An'])
        self.assertEqual(matcher.identify('TânAn')['street'], 'Tân An')
        self.assertIsNone(matcher.identify('TanAn')['street'])
