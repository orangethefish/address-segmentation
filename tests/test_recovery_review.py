import unittest
from address_segmentation import AddressMatcher
from address_segmentation.model import MatcherConfig

class RecoveryReview(unittest.TestCase):
    def test_house_number_is_not_a_numbered_ward(self):
        matcher=AddressMatcher(streets=['Lê Lợi'],wards=['Phường 1'])
        self.assertEqual(matcher.identify('1 Lê Lợi'),{'street':'Lê Lợi','ward':None,'province':None})

    def test_split_ward_number_is_not_a_complete_match(self):
        matcher=AddressMatcher(wards=['Phường 1','Phường 12'])
        self.assertIsNone(matcher.identify('Phường 1 2')['ward'])

    def test_no_comma_exact_tail_cannot_hide_street_typo(self):
        matcher=AddressMatcher(streets=['Nguyễn Trãi'],wards=['Phường Bến Thành'],provinces=['Hà Nội'])
        self.assertEqual(matcher.identify('nguyen tria phuong ben thanh ha noi'),
                         {'street':'Nguyễn Trãi','ward':'Phường Bến Thành','province':'Hà Nội'})

    def test_street_marker_cannot_promote_province_typo(self):
        matcher=AddressMatcher(provinces=['An Phú'])
        self.assertIsNone(matcher.identify('duong an phz')['province'])

    def test_budget_caps_and_independent_exact_evidence(self):
        matcher=AddressMatcher(streets=['Nguyễn Trãi','Nguyễn Trường'],provinces=['Hà Nội'],
                               config=MatcherConfig(max_postings=1))
        result,trace=matcher.identify_with_trace('nguyen tria, Hà Nội')
        self.assertEqual(result['province'],'Hà Nội')
        for name,limit in trace['budgets']['limits'].items():
            self.assertLessEqual(trace['budgets']['counts'][name],limit)
