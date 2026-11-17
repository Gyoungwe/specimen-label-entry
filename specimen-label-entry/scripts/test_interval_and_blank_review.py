"""Do not replace ranges with scalar endpoints; empty correction evidence is valid."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from openpyxl import load_workbook
from build_workbook import build
from label_corrections import repair_batch
from label_intervals import observations
from specimen_data import SKILL,compile_batch
from verify_workbooks import verify,blank_equivalent


def batch():
    records=[]
    for i in [1,2]:
        values={'标本号':f'IOZ(E){i:07d}','具体地点':'示例采集点','采集日期':'2026-07-01',
                '经度':118.1,'纬度':40.2,'海拔':1600}
        records.append({'record_id':f'r{i}','source':'s','page':1,'visually_checked':True,
            'cross_label_reviewed':True,'identification_label_present':False,'collection_label_text':'示例采集点，2026-07-01，1600 m',
            'values':values,'field_quality':{k:'clear' for k in values}})
    return {'sources':{'s':{'name':'synthetic.jpg','page_count':1}},'records':records}


class IntervalAndBlankTests(unittest.TestCase):
    def test_altitude_range_clears_the_ocr_endpoint_and_cannot_borrow_single_value(self):
        for original in ['1600 m - 1800 m','１６００ｍ～１８００ｍ']:
            with self.subTest(original=original):
                data=batch();data['records'][0]['collection_label_text']='示例采集点，2026-07-01，'+original
                before=deepcopy(data);result,report=repair_batch(data)
                first=result['records'][0]
                self.assertIsNone(first['values']['海拔']);self.assertEqual(first['observed_values']['海拔'],1600)
                self.assertEqual(first['label_corrections'][0]['status'],'non_scalar')
                self.assertEqual(first['label_corrections'][0]['original_value'],original);self.assertEqual(data,before)

    def test_range_record_cannot_donate_its_first_endpoint_to_an_unclear_target(self):
        data=batch();data['records'][0]['values']['海拔']=None;data['records'][0]['field_quality']['海拔']='unclear'
        data['records'][1]['collection_label_text']='示例采集点，2026-07-01，1600–1800 m'
        result,report=repair_batch(data)
        self.assertIsNone(result['records'][0]['values']['海拔']);self.assertEqual(report['summary']['applied'],0)

    def test_date_range_is_not_a_single_date_or_a_matching_date_anchor(self):
        data=batch();data['records'][0]['collection_label_text']='2026-07-01 至 07-02，示例采集点，1600 m'
        result,_=repair_batch(data)
        self.assertIsNone(result['records'][0]['values']['采集日期'])
        self.assertIn('采集日期',result['records'][0]['interval_observations'])

    def test_single_iso_date_signed_coordinates_and_scalar_altitude_are_not_ranges(self):
        record={'collection_label_text':'2026-07-01，40.2°N，118.1°E，-20 m',
                'values':{'采集日期':'2026-07-01','经度':-118.1,'海拔':-20}}
        self.assertEqual(observations(record),{})

    def test_explicit_coordinate_range_stays_blank_and_retains_original_text(self):
        data=batch();data['records'][0]['interval_observations']={'经度':{'raw_text':'118.1 至 118.2 E'}}
        result,_=repair_batch(data)
        self.assertIsNone(result['records'][0]['values']['经度'])
        self.assertEqual(result['records'][0]['interval_observations']['经度']['raw_text'],'118.1 至 118.2 E')

    def test_range_without_preexisting_quality_annotations_still_runs_before_compile(self):
        data=batch();data['records']=data['records'][:1];data['records'][0].pop('field_quality')
        data['records'][0]['values']['海拔']='1600–1800 m';data['records'][0]['values']['采集日期']='2026-07-01至07-02'
        rules=json.loads((SKILL/'assets/rules.json').read_text())
        result=compile_batch(data,SKILL/'assets/标本库_模板.xlsx',SKILL/'assets/核心网格.xlsx',rules)
        self.assertIsNone(result['audit'][0]['values']['海拔']);self.assertIsNone(result['audit'][0]['values']['采集日期'])
        self.assertEqual(result['label_correction_summary']['non_scalar'],2)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);main=root/'main.xlsx';review=root/'review.xlsx'
            build(SKILL/'assets/标本库_模板.xlsx',result,main,review,root/'proof')
            self.assertTrue(verify(result,main,review)['structure_and_value_checks_passed'])

    def test_blank_equivalence_preserves_zero_false_and_whitespace(self):
        self.assertIsNone(blank_equivalent(''));self.assertIsNone(blank_equivalent(None))
        self.assertEqual(blank_equivalent(0),0);self.assertIs(blank_equivalent(False),False)
        self.assertEqual(blank_equivalent(' '),' ')

    def test_empty_correction_references_read_back_and_nonempty_tampering_fails(self):
        data=batch();data['records']=data['records'][:1]
        data['records'][0]['values']['海拔']=None;data['records'][0]['field_quality']['海拔']='unclear'
        rules=json.loads((SKILL/'assets/rules.json').read_text())
        compiled=compile_batch(data,SKILL/'assets/标本库_模板.xlsx',SKILL/'assets/核心网格.xlsx',rules)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);main=root/'main.xlsx';review=root/'review.xlsx'
            build(SKILL/'assets/标本库_模板.xlsx',compiled,main,review,root/'proof')
            self.assertTrue(verify(compiled,main,review)['structure_and_value_checks_passed'])
            for value in ['unexpected source',' ',0,'=0']:
                with self.subTest(value=value):
                    book=load_workbook(review);book['标签纠错']['K7']=value;book.save(review)
                    result=verify(compiled,main,review)
                    self.assertFalse(result['structure_and_value_checks_passed'])
                    self.assertTrue(any('标签纠错K7' in error for error in result['errors']))


if __name__=='__main__':unittest.main()
