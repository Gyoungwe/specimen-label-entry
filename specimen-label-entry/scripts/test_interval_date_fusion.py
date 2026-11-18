import unittest
from label_intervals import observations


class DateAltitudeFusionTests(unittest.TestCase):
    def test_fused_date_is_not_an_altitude_interval_or_an_inferred_scalar(self):
        for raw in ('2026-07-03733.00m', '2026-07-02973.00 m', '２０２６-０７-０３７３３.００ｍ'):
            record={'collection_label_text':raw}
            self.assertNotIn('海拔',observations(record))
            self.assertNotIn('values',record)

    def test_actual_altitude_and_date_intervals_keep_original_fragments(self):
        raw='采集日2026-07-011600 m – 1800 m；另2026-07-01 至 07-02'
        result=observations({'collection_label_text':raw})
        self.assertEqual(result['海拔']['raw_text'],'1600 m – 1800 m')
        self.assertEqual(result['采集日期']['raw_text'],'2026-07-01 至 07-02')


if __name__=='__main__':unittest.main()
