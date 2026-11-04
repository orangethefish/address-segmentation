"""Evaluation traces must distinguish candidate loss from selection rejection."""
import unittest
from address_segmentation import AddressMatcher
from address_segmentation.model import MatcherConfig
from tools.datasets import EvaluationCase
from tools.evaluate import score_retrieval

class DiagnosticTests(unittest.TestCase):
    def test_retrieval_and_selection_losses_are_distinct(self):
        cases = [EvaluationCase('a', 'tan an', {'street':'Tân An','ward':None,'province':None}, (), 'a','recoverable'),
                 EvaluationCase('b', 'missing', {'street':'Tân An','ward':None,'province':None}, (), 'b','recoverable')]
        matcher = AddressMatcher(streets=['Tân An','Tấn An'])
        predictions, traces = {}, {}
        for case in cases:
            predictions[case.id], traces[case.id] = matcher.identify_with_trace(case.address)
        metrics = score_retrieval(cases,predictions,traces)
        self.assertEqual(metrics['recall_before_verification']['numerator'],1)
        self.assertEqual(metrics['recall_before_verification']['denominator'],2)
        self.assertEqual([x['first_loss'] for x in metrics['failures']],['selection','retrieved'])

    def test_low_evidence_cannot_influence_another_field(self):
        matcher = AddressMatcher(streets=['Hòa Bình'], provinces=['Hóa Bình'],
                                 config=MatcherConfig(min_score=80))
        self.assertEqual(matcher.identify('Hòa Bình'),
                         {'street':'Hòa Bình','ward':None,'province':None})
