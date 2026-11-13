#!/usr/bin/env python3
"""Behavior checks for containment, missing evidence and specimen defaults."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from specimen_data import classify_point, compile_batch, load_grids, match_grid, edge_distance_m, SKILL
from template_checks import check_values, check_rule, read_contract, plan_parts, summarize


class SpatialTests(unittest.TestCase):
    def test_distances_are_review_metrics_without_changing_membership(self):
        polygon=[(0,0),(1,0),(1,1),(0,1)]
        self.assertAlmostEqual(edge_distance_m(.5,0,polygon),0)
        self.assertAlmostEqual(edge_distance_m(.5,.001,polygon),110.94,places=3)
        result=match_grid(.5,.001,[{'id':'A','polygon':polygon,'sources':[]}])
        self.assertEqual(result['id'],'A')
        self.assertEqual(result['edge_distance_m'],110.94)

    def test_conflicting_same_id_geometry_is_not_silently_selected(self):
        header=['编号']+[f'顶点{i}{axis}' for i in range(1,5) for axis in ['经度','纬度']]
        sheets=[{'sheet':'A','rows':[(1,header),(2,['X',0,0,1,0,1,1,0,1])]},
                {'sheet':'B','rows':[(1,header),(2,['X',1,0,2,0,2,1,1,1])]}]
        with patch('specimen_data.read_xlsx',return_value=sheets),self.assertRaises(ValueError):
            load_grids('synthetic.xlsx')

    def test_rotated_grid_is_not_its_bounding_box(self):
        diamond = [(1,0),(2,1),(1,2),(0,1)]
        self.assertEqual(classify_point(1,1,diamond), "inside")
        self.assertEqual(classify_point(.1,.1,diamond), "outside")
        self.assertEqual(classify_point(.5,.5,diamond), "boundary")

    def test_boundary_ambiguity_and_uncovered_point_remain_blank(self):
        grids = [{"id":"a01","polygon":[(0,0),(1,0),(1,1),(0,1)],"sources":[]},
                 {"id":"A01","polygon":[(1,0),(2,0),(2,1),(1,1)],"sources":[]}]
        self.assertIsNone(match_grid(1,.5,grids)["id"])
        self.assertEqual(match_grid(1,.5,grids)["status"], "boundary")
        self.assertEqual(match_grid(4,4,grids)["status"], "unmatched")
        self.assertEqual(match_grid(None,.5,grids)["status"], "missing_coordinates")
        overlap = [grids[0],dict(grids[0],id="other")]
        self.assertEqual(match_grid(.5,.5,overlap)["status"], "ambiguous")

    def test_real_label_points(self):
        grids = load_grids(SKILL/'assets/核心网格.xlsx')
        self.assertEqual(match_grid(113.89129931,38.41597533,grids)["id"], "X09")
        self.assertEqual(match_grid(114.28345446,38.22001159,grids)["id"], "Y11")
        self.assertEqual(match_grid(114.10977387,37.52451778,grids)["status"], "unmatched")
        with self.assertRaises(ValueError):
            match_grid(38.41597533,113.89129931,grids)


class RuleTests(unittest.TestCase):
    def setUp(self):
        self.rules = json.loads((SKILL/'assets/rules.json').read_text())
        self.batch = {"sources":{"s":{"name":"test.pdf","page_count":1}}, "records":[{
            "source":"s","page":1,"visually_checked":True,"identification_label_present":False,
            "collection_label_text":"测试采集标签，无采集方式", "values":{"标本号":"IOZ(E)0000001","省":"河北省",
            "经度":114.28345446,"纬度":38.22001159,"序列号":999,"Id":"invented"}}]}

    def compile(self, batch=None):
        return compile_batch(batch or self.batch,SKILL/'assets/标本库_模板.xlsx',SKILL/'assets/核心网格.xlsx',self.rules)

    def test_requested_defaults_and_explicit_light_exception(self):
        result = self.compile()['audit'][0]['values']
        self.assertIsNone(result['序列号'])
        self.assertIsNone(result['Id'])
        self.assertEqual(result['采集号'],result['标本号'])
        self.assertEqual(result['保藏状况'],'完整')
        self.assertEqual(result['省代码'],'HEB')
        self.assertEqual(result['采集方式'],'扫网')
        self.batch['records'][0]['collection_label_text'] = '某村灯诱'
        self.assertEqual(self.compile()['audit'][0]['values']['采集方式'],'灯诱')

    def test_absent_identification_needs_real_confirmation(self):
        self.batch['records'][0]['values']['种名'] = 'Chrysoperla nipponensis'
        self.batch['records'][0]['identification_evidence'] = None
        with self.assertRaises(ValueError):
            self.compile()
        self.batch['records'][0]['identification_evidence'] = {'type':'user_confirmation','statement':'该标本已由用户明确确认学名'}
        self.assertEqual(self.compile()['audit'][0]['values']['种名'],'Chrysoperla nipponensis')

    def test_county_differences_and_near_edges_are_review_only(self):
        self.batch['records'][0]['values'].update({'经度':113.89129931,'纬度':38.41597533,'区县':'测试县'})
        result=self.compile()['audit'][0]
        self.assertEqual(result['values']['区县'],'测试县')
        self.assertEqual(result['values']['网格号'],'X09')
        self.assertIn('county',{flag['kind'] for flag in result['flags']})
        self.assertIn('near_edge',{flag['kind'] for flag in result['flags']})
        self.rules['grid_review_distance_m']=None
        result=self.compile()['audit'][0]
        self.assertNotIn('near_edge',{flag['kind'] for flag in result['flags']})
        self.assertEqual(result['values']['网格号'],'X09')

    def test_missing_pages_duplicate_ids_and_unreviewed_records_are_visible(self):
        missing = copy.deepcopy(self.batch)
        missing['sources']['s']['page_count'] = 2
        with self.assertRaises(ValueError):
            self.compile(missing)
        duplicate = copy.deepcopy(self.batch)
        duplicate['records'].append(copy.deepcopy(duplicate['records'][0]))
        with self.assertRaises(ValueError):
            self.compile(duplicate)
        self.batch['records'][0]['visually_checked'] = False
        with self.assertRaises(ValueError):
            self.compile()

    def test_capacity_splits_250_records_and_checks_each_local_output_row(self):
        self.batch['sources']['s']['page_count']=250
        prototype=self.batch['records'][0]
        records=[]
        for i in range(1,251):
            record=copy.deepcopy(prototype)
            record['page']=i
            record['values']['标本号']=f'TEST{i:04d}'
            records.append(record)
        self.batch['records']=records
        result=self.compile()
        self.assertEqual([p['count'] for p in result['parts']],[249,1])
        self.assertEqual((result['audit'][248]['part'],result['audit'][248]['excel_row']),(1,250))
        self.assertEqual((result['audit'][249]['part'],result['audit'][249]['excel_row']),(2,2))
        self.assertEqual(result['validation_summary']['conflict'],250)
        self.assertTrue(all(check['excel_row']<=250 for check in result['validation_checks']))


class TemplateTests(unittest.TestCase):
    def setUp(self):
        self.contract={'sheet':'Records','names':{},'date1904':False,'rules':[]}
        self.rule={'id':1,'type':'list','operator':'between','ranges':[(0,2,0,3),(2,2,2,2)],
                   'formula1':'"1.完整, 2.受损"','formula2':''}

    def test_checks_all_actual_target_ranges_and_preserves_conflicting_user_text(self):
        self.contract['rules']=[self.rule]
        checks=check_values({'a':'完整','b':'无约束','c':'2.受损'},['a','b','c'],2,self.contract,{}, {'a':'完整'})
        self.assertEqual([(x['cell'],x['status']) for x in checks],[('A2','conflict'),('C2','passed')])
        self.assertEqual(checks[0]['value'],'完整')
        outside=check_values({'a':'坏值','c':'坏值'},['a','b','c'],4,self.contract,{}, {})
        self.assertEqual(outside,[])

    def test_named_list_ranges_can_include_zero(self):
        self.contract['names']={'Choices':"'Lookup Sheet'!$A$1:$A$3"}
        rule=dict(self.rule,formula1='=Choices')
        tables={'Lookup Sheet':[(1,['普通']),(2,[0]),(3,['1.完整'])]}
        self.assertTrue(check_rule(0,rule,self.contract,tables)[0])
        self.assertFalse(check_rule('完整',rule,self.contract,tables)[0])

    def test_numeric_types_bounds_and_dates(self):
        rule=dict(self.rule,type='whole',formula1='0',formula2='10')
        self.assertTrue(check_rule(0,rule,self.contract,{})[0])
        self.assertFalse(check_rule('3',rule,self.contract,{})[0])
        self.assertFalse(check_rule(3.5,rule,self.contract,{})[0])
        self.assertFalse(check_rule(11,rule,self.contract,{})[0])
        rule=dict(rule,type='date',formula1='46000',formula2='47000')
        self.assertTrue(check_rule('2026-08-29',rule,self.contract,{})[0])
        self.assertFalse(check_rule('2026-02-30',rule,self.contract,{})[0])

    def test_unsupported_formulas_are_reported_separately(self):
        self.contract['rules']=[dict(self.rule,type='custom',formula1='ISNUMBER(A2)')]
        checks=check_values({'a':1},['a'],2,self.contract,{}, {})
        result=summarize(checks)
        self.assertEqual(result['unsupported'],1)
        self.assertEqual(result['passed'],0)

    def test_capacity_comes_from_real_template_validation_ranges(self):
        contract=read_contract(SKILL/'assets/标本库_模板.xlsx','biaobenku',64)
        self.assertEqual(len(contract['rules']),15)
        self.assertEqual(contract['max_records_per_file'],249)
        self.assertEqual([p['count'] for p in plan_parts(499,249)],[249,249,1])


if __name__ == '__main__':
    unittest.main()
