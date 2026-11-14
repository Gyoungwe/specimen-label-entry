#!/usr/bin/env python3
"""Check evidence-based recovery, conflicts, provenance and exported review cells."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from openpyxl import load_workbook
from build_workbook import build
from label_corrections import repair_batch
from photo_records import join_records
from specimen_data import SKILL, compile_batch
from verify_workbooks import verify


class LabelCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.rules=json.loads((SKILL/'assets/rules.json').read_text())
        values={'标本号':'DEMO-001','国家':'中国','省':'河北省','具体地点':'示例采集点A',
                '采集日期':'2026-06-28','经度':114.28345446,'纬度':38.22001159,'海拔':325,'采集人':'示例采集人'}
        donor={'record_id':'reference','source':'photo','page':1,'visually_checked':True,
               'identification_label_present':False,'collection_label_text':'示例采集点A，2026-06-28，114.28345446 E，38.22001159 N',
               'values':values,'field_quality':{field:'clear' for field in values}}
        target=deepcopy(donor)
        target.update(record_id='target',cross_label_reviewed=True,label_quality={'collection':'damaged'},
                      collection_label_text='示例采集点A，2026-06-28，经度114.28??，海拔破损')
        target['values'].update({'标本号':'DEMO-002','经度':'114.28??','海拔':None})
        target['field_quality'].update({'经度':'unclear','海拔':'damaged'})
        self.batch={'sources':{'photo':{'name':'example.jpg','page_count':1,'sha256':'demo-source-sha'}},'records':[target,donor]}

    def repaired(self):
        return repair_batch(self.batch,self.rules)

    def compile(self):
        return compile_batch(self.batch,SKILL/'assets/标本库_模板.xlsx',SKILL/'assets/核心网格.xlsx',self.rules)

    def test_recovery_preserves_raw_text_observations_and_source(self):
        before=deepcopy(self.batch)
        repaired,report=self.repaired()
        target=repaired['records'][0]
        self.assertEqual(target['values']['经度'],114.28345446)
        self.assertEqual(target['values']['海拔'],325)
        self.assertEqual(target['observed_values']['经度'],'114.28??')
        self.assertEqual(target['collection_label_text'],before['records'][0]['collection_label_text'])
        self.assertEqual(report['summary']['applied'],2)
        self.assertEqual(self.batch,before)
        donor=target['label_corrections'][0]['donors'][0]
        self.assertEqual((donor['record_id'],donor['source_name'],donor['source_sha256']),('reference','example.jpg','demo-source-sha'))

    def test_repeated_runs_recompute_evidence_and_remain_idempotent(self):
        first,report1=self.repaired()
        second,report2=repair_batch(first,self.rules)
        self.assertEqual(first,second)
        self.assertEqual(report1,report2)
        first['records'][1]['values']['经度']=117
        first['records'][1]['observed_values']['经度']=117
        third,_=repair_batch(first,self.rules)
        self.assertEqual(third['records'][0]['values']['经度'],117)
        self.assertEqual(third['records'][0]['observed_values']['经度'],'114.28??')

    def test_conflicting_clear_references_do_not_use_majority_vote(self):
        donor=deepcopy(self.batch['records'][1])
        donor['record_id']='conflicting'
        donor['values'].update({'标本号':'DEMO-003','经度':114.28})
        self.batch['records'].append(donor)
        agreeing=deepcopy(self.batch['records'][1])
        agreeing['record_id']='agreeing'
        agreeing['values']['标本号']='DEMO-004'
        self.batch['records'].append(agreeing)
        result,report=self.repaired()
        self.assertIsNone(result['records'][0]['values']['经度'])
        self.assertEqual(report['summary']['ambiguous'],1)
        self.assertEqual(len(result['records'][0]['label_corrections'][0]['candidates']),2)

    def test_catalogue_and_identification_are_not_borrowed_from_other_individuals(self):
        target,donor=self.batch['records']
        target['values'].update({'标本号':'DEMO-00?','种名':'Unclear species'})
        target['field_quality'].update({'标本号':'unclear','种名':'damaged'})
        donor['values']['种名']='Clear species'
        donor['field_quality']['种名']='clear'
        result,_=self.repaired()
        self.assertIsNone(result['records'][0]['values']['标本号'])
        self.assertIsNone(result['records'][0]['values']['种名'])
        protected=[c for c in result['records'][0]['label_corrections'] if c['field'] in ['标本号','种名']]
        self.assertTrue(all(c['reason_code']=='individual_field' for c in protected))

    def test_existing_user_confirmed_identification_survives_missing_label_quality(self):
        target=self.batch['records'][0]
        target['values']['种名']='Confirmed example'
        target['label_quality']['identification']='missing'
        target['identification_evidence']={'type':'user_confirmation','statement':'用户明确确认该标本学名为 Confirmed example'}
        result=self.compile()
        self.assertEqual(result['audit'][0]['values']['种名'],'Confirmed example')
        item=next(c for c in result['audit'][0]['label_corrections'] if c['field']=='种名')
        self.assertEqual(item['reason_code'],'user_identification_confirmation')
        self.assertEqual(item['donors'],[])

    def test_candidate_requires_visual_cross_comparison_before_filling(self):
        self.batch['records'][0]['cross_label_reviewed']=False
        result,report=self.repaired()
        self.assertIsNone(result['records'][0]['values']['经度'])
        self.assertEqual(report['summary']['applied'],0)
        self.assertEqual(result['records'][0]['label_corrections'][0]['reason_code'],'visual_comparison_pending')
        self.assertEqual(len(result['records'][0]['label_corrections'][0]['candidates']),1)

    def test_unreviewed_or_unannotated_reference_is_not_a_clear_source(self):
        for change in [{'visually_checked':False},{'field_quality':{}}]:
            with self.subTest(change=change):
                original=deepcopy(self.batch)
                self.batch['records'][1].update(change)
                result,report=self.repaired()
                self.assertEqual(report['summary']['applied'],0)
                self.assertIsNone(result['records'][0]['values']['经度'])
                self.batch=original

    def test_whole_clear_label_can_annotate_reference_fields(self):
        donor=self.batch['records'][1]
        donor.pop('field_quality')
        donor['label_quality']={'collection':'clear'}
        result,report=self.repaired()
        self.assertEqual(report['summary']['applied'],2)
        self.assertEqual(result['records'][0]['values']['经度'],114.28345446)

    def test_reference_without_any_original_label_text_is_not_used(self):
        self.batch['records'][1]['collection_label_text']=None
        result,report=self.repaired()
        self.assertEqual(report['summary']['applied'],0)
        self.assertIsNone(result['records'][0]['values']['经度'])

    def test_damaged_label_does_not_leave_unannotated_ocr_values_as_clear_facts(self):
        target=self.batch['records'][0]
        target['field_quality']={'具体地点':'clear','采集日期':'clear'}
        target['values']['纬度']='38.22??'
        result,report=self.repaired()
        self.assertEqual(result['records'][0]['field_quality']['纬度'],'damaged')
        self.assertEqual(result['records'][0]['values']['纬度'],38.22001159)
        self.assertEqual(result['records'][0]['observed_values']['纬度'],'38.22??')
        self.assertGreater(report['summary']['applied'],2)

    def test_damaged_scale_label_is_flagged_without_modifying_geometry(self):
        target=self.batch['records'][0]
        target['label_quality']['scale']='unclear'
        result,_=self.repaired()
        items=result['records'][0]['label_corrections']
        self.assertTrue(any(item.get('label_role')=='scale' and item['status']=='pending' for item in items))

    def test_recovered_values_cannot_supply_new_reference_anchors(self):
        target,donor=self.batch['records']
        donor['values']['纬度']=None
        donor['field_quality']['纬度']='missing'
        donor['cross_label_reviewed']=True
        third=deepcopy(target)
        third.update(record_id='third',collection_label_text='地点破损，其余清晰')
        third['values'].update({'标本号':'DEMO-003','具体地点':None,'经度':114.28345446,'海拔':325})
        third['field_quality'].update({'具体地点':'damaged','经度':'clear','海拔':'clear'})
        self.batch['records'].append(third)
        result,_=self.repaired()
        self.assertEqual(result['records'][0]['values']['经度'],114.28345446)
        self.assertIsNone(result['records'][2]['values']['具体地点'])

    def test_readable_context_conflict_blocks_otherwise_matching_donor(self):
        self.batch['records'][0]['values']['采集人']='另一个采集人'
        result,report=self.repaired()
        self.assertEqual(report['summary']['applied'],0)
        self.assertIsNone(result['records'][0]['values']['经度'])

    def test_same_photo_province_date_or_adjacent_ids_are_insufficient(self):
        self.batch['records'][0]['field_quality']['具体地点']='unclear'
        self.batch['records'][0]['values']['具体地点']='示例采集点?'
        result,report=self.repaired()
        self.assertEqual(report['summary']['applied'],0)
        self.assertIsNone(result['records'][0]['values']['经度'])

    def test_confirmed_collection_group_can_recover_date_with_no_date_anchor(self):
        target=self.batch['records'][0]
        target['values']['采集日期']='2026-06-??'
        target['field_quality']['采集日期']='damaged'
        self.batch['shared_collection_groups']=[{'id':'event-one','record_ids':['target','reference'],
            'fields':['采集日期','经度','海拔'],'evidence':{'type':'user_confirmation','statement':'用户确认这两枚共享同一采集事件'}}]
        result,report=self.repaired()
        self.assertEqual(result['records'][0]['values']['采集日期'],'2026-06-28')
        self.assertEqual(report['summary']['applied'],3)

    def test_model_guessed_group_and_individual_shared_fields_are_rejected(self):
        group={'id':'event-one','record_ids':['target','reference'],'fields':['采集日期'],
               'evidence':{'type':'model_guess','statement':'看起来一样'}}
        self.batch['shared_collection_groups']=[group]
        with self.assertRaises(ValueError):self.repaired()
        group['evidence']={'type':'user_confirmation','statement':'用户明确确认'}
        group['fields']=['标本号']
        with self.assertRaises(ValueError):self.repaired()

    def test_incomplete_dates_and_coordinates_are_cleared_before_compiler_validation(self):
        target=self.batch['records'][0]
        target['values']['采集日期']='2026-??-28'
        target['field_quality']['采集日期']='unclear'
        compiled=self.compile()
        self.assertIsNone(compiled['audit'][0]['values']['采集日期'])
        self.assertIsNone(compiled['audit'][0]['values']['经度'])
        self.assertEqual(compiled['audit'][0]['grid']['status'],'missing_coordinates')

    def test_grid_is_recomputed_after_longitude_recovery(self):
        result=self.compile()
        self.assertEqual(result['audit'][0]['grid']['id'],'Y11')
        self.assertTrue(any(f['kind']=='label_correction' and f['field']=='经度' for f in result['audit'][0]['flags']))
        self.assertIn('DEMO-001',result['audit'][0]['values']['备注'])

    def test_missing_entire_label_keeps_raw_missing_and_collection_method_rule(self):
        target,donor=self.batch['records']
        target['collection_label_text']=None
        target['label_quality']['collection']='missing'
        target['field_quality']['采集方式']='damaged'
        donor['collection_label_text']+='，灯诱'
        result=self.compile()
        self.assertIsNone(result['audit'][0]['collection_label_text'])
        self.assertEqual(result['audit'][0]['values']['采集方式'],'扫网')
        self.assertEqual(result['audit'][1]['values']['采集方式'],'灯诱')
        self.assertGreater(result['label_correction_summary']['rule_managed'],0)

    def test_legacy_batch_remains_unchanged_without_quality_annotations(self):
        donor=deepcopy(self.batch['records'][1])
        donor.pop('field_quality')
        self.batch['records']=[donor]
        result,report=self.repaired()
        self.assertEqual(result,self.batch)
        self.assertFalse(report['summary']['enabled'])
        self.assertEqual(report['items'],[])

    def test_invalid_quality_status_or_unknown_template_field_is_rejected(self):
        self.batch['records'][0]['field_quality']['经度']='looks-clear'
        with self.assertRaises(ValueError):self.repaired()
        self.batch['records'][0]['field_quality']['经度']='unclear'
        self.batch['records'][0]['field_quality']['经杜']='unclear'
        with self.assertRaisesRegex(ValueError,'Unknown quality'):self.compile()

    def test_label_level_quality_without_field_reading_creates_pending_item(self):
        self.batch['records'][0].pop('field_quality')
        self.batch['records'][0]['values']={'标本号':'DEMO-002'}
        result,report=self.repaired()
        self.assertEqual(report['summary']['applied'],0)
        self.assertEqual(result['records'][0]['label_corrections'][0]['reason_code'],'field_annotation_needed')

    def test_export_review_sheet_and_detect_modified_correction_provenance(self):
        data=self.compile()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            main,review=root/'main.xlsx',root/'review.xlsx'
            build(SKILL/'assets/标本库_模板.xlsx',data,main,review,root/'preview')
            result=verify(data,main,review)
            self.assertTrue(result['structure_and_value_checks_passed'],result['errors'])
            self.assertEqual(result['label_correction_rows'],2)
            book=load_workbook(review)
            self.assertEqual(book.sheetnames,['逐条核对','模板校验','标签纠错'])
            self.assertIsNone(book['标签纠错']['N7'].value)
            self.assertIsNone(book['标签纠错']['O7'].value)
            self.assertEqual(book['标签纠错']['I7'].value,'114.28345446')
            self.assertEqual(book['标签纠错']['I7'].number_format,'0.00000000')
            book['标签纠错']['K7']='changed reference'
            book.save(review)
            book.close()
            result=verify(data,main,review)
            self.assertFalse(result['structure_and_value_checks_passed'])
            self.assertTrue(any('标签纠错K7' in error for error in result['errors']))

    def test_photo_bridge_retains_quality_and_confirmed_group_metadata(self):
        manifest={'source':{'path':'demo.jpg','sha256':'demo-source-sha'},'groups':[]}
        transcription={'records':[],'shared_collection_groups':[]}
        for record in self.batch['records']:
            item=deepcopy(record)
            sid=item['values']['标本号']
            item['specimen_number']=sid
            manifest['groups'].append({'specimen_number':sid,'pairing_reviewed':True,'identification_label_present':False,
                                        'group_box':[0,0,10,10],'path':sid+'.png','components':{}})
            transcription['records'].append(item)
        transcription['records'][0]['collection_label_text']=None
        transcription['shared_collection_groups']=[{'id':'example','record_ids':['target','reference'],
            'fields':['经度'],'evidence':{'type':'user_confirmation','statement':'用户确认这两枚共享采集信息'}}]
        result=join_records(manifest,transcription)
        self.assertEqual(result['records'][0]['field_quality']['经度'],'unclear')
        self.assertEqual(result['records'][0]['record_id'],'target')
        self.assertEqual(result['shared_collection_groups'],transcription['shared_collection_groups'])

    def test_cli_preserves_inputs_and_refuses_existing_outputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            source=root/'input.json'
            source.write_text(json.dumps(self.batch,ensure_ascii=False))
            before=source.read_bytes()
            command=[sys.executable,str(SKILL/'scripts/label_corrections.py'),str(source),
                     '--out',str(root/'corrected.json'),'--report',str(root/'report.json')]
            result=subprocess.run(command,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout)['applied'],2)
            self.assertEqual(source.read_bytes(),before)
            result=subprocess.run(command,capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)


if __name__=='__main__':
    unittest.main()
