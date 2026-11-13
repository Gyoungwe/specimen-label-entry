#!/usr/bin/env python3
"""Behavior checks for the cross-host workbook writer and template preservation."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile
from lxml import etree as E
from openpyxl import load_workbook
from build_workbook import build, part_path
from specimen_data import SKILL, compile_batch
from verify_workbooks import verify
from xlsx_copy import digest, NS, S


class PortableWorkbookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='标本 portable ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.template = SKILL / 'assets/标本库_模板.xlsx'
        self.grid = SKILL / 'assets/核心网格.xlsx'
        self.rules = json.loads((SKILL / 'assets/rules.json').read_text())
        self.batch = {'sources': {'s': {'name': 'test source.jpg', 'page_count': 1}}, 'records': [{
            'source': 's', 'page': 1, 'visually_checked': True, 'identification_label_present': False,
            'collection_label_text': '中国，河北省，某地，无采集方式',
            'values': {'标本号': 'IOZ(E)0000001', '省': '河北省', '采集人': '=SUM(1,2)',
                       '采集日期': '2026-06-28', '经度': 118.38031, '纬度': 40.55756, '海拔': 329.01}}]}
        self.output = self.root / '结果 main.xlsx'
        self.review = self.root / '核对 review.xlsx'
        self.evidence = self.root / 'evidence'

    def compile(self, template=None):
        return compile_batch(self.batch, template or self.template, self.grid, self.rules)

    def write(self, data=None, template=None):
        return build(template or self.template, data or self.compile(), self.output, self.review, self.evidence)

    def test_copy_before_filling_native_validation_dates_and_exact_values(self):
        data = self.compile()
        before = digest(self.template)
        self.write(data)
        result = verify(data, self.output, self.review)
        self.assertTrue(result['structure_and_value_checks_passed'], result['errors'])
        self.assertEqual(result['native_validation_rules_preserved_per_main_file'], 15)
        self.assertEqual(digest(self.template), before)
        evidence = json.loads((self.evidence / 'template-copy-evidence.json').read_text())
        self.assertTrue(evidence[0]['copiedBeforeFilling'])
        self.assertEqual(evidence[0]['copiedSha256'], before)
        self.assertNotEqual(evidence[0]['filledSha256'], before)
        self.assertTrue(evidence[0]['preservation']['unrelated_members_byte_identical'])

    def test_formula_like_strings_remain_exact_literal_text_in_both_books(self):
        self.batch['sources']['s']['name'] = '=HYPERLINK("bad")'
        data = self.compile()
        self.write(data)
        main = load_workbook(self.output)
        cell = main[data['sheet']].cell(2, data['headers'].index('采集人') + 1)
        self.assertEqual(cell.value, '=SUM(1,2)')
        self.assertEqual(cell.data_type, 's')
        review = load_workbook(self.review)
        self.assertEqual(review['逐条核对']['A7'].value, self.batch['sources']['s']['name'])
        self.assertEqual(review['逐条核对']['A7'].data_type, 's')
        main.close()
        review.close()

    def test_unrelated_xlsx_members_and_validation_xml_are_preserved(self):
        self.write()
        with ZipFile(self.template) as original, ZipFile(self.output) as result:
            self.assertEqual(original.namelist(), result.namelist())
            for name in original.namelist():
                if name not in ('xl/worksheets/sheet1.xml', 'xl/styles.xml'):
                    self.assertEqual(original.read(name), result.read(name), name)
            before = E.fromstring(original.read('xl/worksheets/sheet1.xml'))
            after = E.fromstring(result.read('xl/worksheets/sheet1.xml'))
            for name in ['dataValidations', 'sheetViews', 'pageSetup', 'headerFooter', 'dimension']:
                self.assertEqual(E.tostring(before.find(f's:{name}', NS)), E.tostring(after.find(f's:{name}', NS)), name)

    def test_250_records_split_without_falling_outside_validation_ranges(self):
        prototype = self.batch['records'][0]
        self.batch['records'] = []
        self.batch['sources']['s']['page_count'] = 250
        for index in range(1, 251):
            record = copy.deepcopy(prototype)
            record['page'] = index
            record['values']['标本号'] = f'TEST{index:07d}'
            self.batch['records'].append(record)
        data = self.compile()
        self.write(data)
        self.assertEqual([p['count'] for p in data['parts']], [249, 1])
        self.assertTrue(part_path(self.output, 1, 2).is_file())
        self.assertTrue(part_path(self.output, 2, 2).is_file())
        result = verify(data, self.output, self.review)
        self.assertTrue(result['structure_and_value_checks_passed'], result['errors'][:3])

    def test_existing_output_is_preserved(self):
        self.output.write_bytes(b'keep existing user output')
        with self.assertRaises(FileExistsError):
            self.write()
        self.assertEqual(self.output.read_bytes(), b'keep existing user output')

    def test_reference_cannot_be_an_output(self):
        self.output = self.template
        before = digest(self.template)
        with self.assertRaises(ValueError):
            self.write()
        self.assertEqual(digest(self.template), before)

    def test_inconsistent_matrix_and_part_mapping_are_rejected_before_copy(self):
        data = self.compile()
        bad = copy.deepcopy(data)
        bad['rows'][0][0] = 'unexpected'
        with self.assertRaises(ValueError):
            self.write(bad)
        self.assertFalse(self.output.exists())
        bad = copy.deepcopy(data)
        bad['parts'][0]['count'] += 1
        with self.assertRaises(ValueError):
            self.write(bad)
        self.assertFalse(self.output.exists())

    def test_formula_in_an_unused_template_data_row_is_not_overwritten(self):
        altered = self.root / 'populated.xlsx'
        with ZipFile(self.template) as original, ZipFile(altered, 'w') as result:
            for entry in original.infolist():
                content = original.read(entry.filename)
                if entry.filename == 'xl/worksheets/sheet1.xml':
                    root = E.fromstring(content)
                    cell = root.find('s:sheetData/s:row[@r="200"]/s:c[@r="A200"]', NS)
                    self.assertIsNotNone(cell)
                    E.SubElement(cell, f'{{{S}}}f').text = '1+1'
                    content = E.tostring(root, xml_declaration=True, encoding='UTF-8')
                result.writestr(entry, content)
        data = self.compile(altered)
        before = digest(altered)
        with self.assertRaisesRegex(ValueError, 'populated'):
            self.write(data, altered)
        self.assertEqual(digest(altered), before)

    def test_all_passed_validation_keeps_empty_problem_sheet_and_human_columns(self):
        self.rules['fixed']['保藏状况'] = '1.完整'
        self.batch['records'][0]['values'].update({'经度': 114.28345446, '纬度': 38.22001159})
        data = self.compile()
        self.assertEqual(data['validation_summary']['conflict'], 0)
        self.write(data)
        result = verify(data, self.output, self.review)
        self.assertTrue(result['structure_and_value_checks_passed'], result['errors'])
        review = load_workbook(self.review)
        self.assertEqual(review['模板校验'].max_row, 7)
        self.assertIsNone(review['逐条核对']['N7'].value)
        self.assertIsNone(review['逐条核对']['O7'].value)
        review.close()


if __name__ == '__main__':
    unittest.main()
