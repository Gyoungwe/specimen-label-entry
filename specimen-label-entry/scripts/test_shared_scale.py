#!/usr/bin/env python3
"""Twelve-group coverage and source-pixel preservation for a shared scale."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from PIL import Image, ImageDraw
from photo_groups import digest, pixel_box, split, verify as verify_crops
from photo_records import join_records
from shared_scale import add, append_scale, compose_target, equal_region, verify


def fixture(root):
    source=root/'synthetic-12.png'
    image=Image.new('RGB',(1600,1200),'#F3F1EA')
    draw=ImageDraw.Draw(image)
    plan={'source_sha256':None,'display_size':list(image.size),'coordinate_space':'normalized_0_1000',
          'expected_groups':12,'shared_scale':{'box':[40,820,280,950],
            'label_text':'10 mm','visually_checked':True},'groups':[]}
    for n in range(12):
        x=(n%4)*250+20;y=(n//4)*250+20
        sid=f'TEST-{n+1:02d}'
        components={'specimen':[x,y,x+60,y+50],'catalogue':[x+80,y+60,x+180,y+100],
                    'collection':[x,y+120,x+180,y+190],'identification':None,'scale':None}
        colour=(60+n*9,75+n*5,100+n*3)
        draw.ellipse(pixel_box(components['specimen'],*image.size),fill=colour)
        for role in ('catalogue','collection'):
            box=pixel_box(components[role],*image.size)
            draw.rectangle(box,outline='black',width=2)
            draw.text((box[0]+6,box[1]+6),sid if role=='catalogue' else 'SYNTHETIC TEST LABEL',fill='black')
        plan['groups'].append({'specimen_number':sid,'id_verified':True,'pairing_reviewed':True,
            'identification_label_present':False,'components':components,'issues':[]})
    scale=pixel_box(plan['shared_scale']['box'],*image.size)
    draw.text((scale[0]+20,scale[1]+20),'10 mm - SYNTHETIC TEST',fill='black')
    draw.line((scale[0]+20,scale[3]-30,scale[2]-20,scale[3]-30),fill='black',width=4)
    image.save(source)
    plan['source_sha256']=digest(source)
    return source,plan


class SharedScaleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.source,self.plan=fixture(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def export(self):
        path=self.root/'plan.json';path.write_text(json.dumps(self.plan))
        split(self.source,path,self.root/'raw')
        return self.root/'raw/groups-manifest.json'

    def test_twelve_groups_one_scale_and_twenty_four_compositions(self):
        manifest=self.export()
        before=digest(self.source)
        checks=verify_crops(manifest)
        self.assertEqual((checks['groups'],checks['shared_scale_crops'],checks['pixel_identical_crops']),(12,1,49))
        result=add(manifest,self.root/'composed')
        self.assertEqual(len(result['images']),24)
        self.assertEqual(verify(self.root/'composed/compositions-manifest.json')['scale_copies_verified'],24)
        self.assertEqual(digest(self.source),before)
        self.assertEqual(len(list((self.root/'raw').glob('shared-scale*.png'))),1)

    def test_missing_twelfth_group_is_rejected(self):
        self.plan['groups'].pop()
        with self.assertRaisesRegex(ValueError,'Group count'):
            self.export()

    def test_global_scale_does_not_belong_to_each_group_box(self):
        manifest=json.loads(self.export().read_text())
        scale=manifest['shared_scale']['box']
        self.assertTrue(all(g['group_box'][3]<scale[1] for g in manifest['groups']))

    def test_reject_unreviewed_or_overlapping_scale(self):
        self.plan['shared_scale']['visually_checked']=False
        with self.assertRaisesRegex(ValueError,'Inspect the common'):
            self.export()
        self.plan['shared_scale']['visually_checked']=True
        self.plan['shared_scale']['box']=self.plan['groups'][0]['components']['specimen']
        with self.assertRaisesRegex(ValueError,'overlaps'):
            self.export()

    def test_shared_and_local_scales_do_not_get_mixed(self):
        self.plan['groups'][0]['components']['scale']=[40,820,280,950]
        with self.assertRaisesRegex(ValueError,'photo level'):
            self.export()

    def test_narrow_base_is_padded_not_scaled_in_all_supported_modes(self):
        for mode,colour in [('RGB',(1,20,50)),('RGBA',(1,20,50,70)),('L',60)]:
            with self.subTest(mode=mode):
                base=Image.new(mode,(20,30),colour)
                scale=Image.new(mode,(180,50),colour)
                image,base_box,scale_box=append_scale(base,scale)
                self.assertGreaterEqual(image.width,scale.width+48)
                self.assertTrue(equal_region(image,base_box,base))
                self.assertTrue(equal_region(image,scale_box,scale))

    def test_already_visible_shared_scale_is_not_added_twice(self):
        base=Image.new('RGB',(300,200),'white')
        scale=Image.new('RGB',(80,30),'black')
        base.paste(scale,(200,120))
        group={'group_box':[10,20,310,220]};shared={'box':[210,140,290,170]}
        image,base_box,scale_box,placement=compose_target(base,scale,group,'group',shared)
        self.assertEqual(placement,'original_scale_visible')
        self.assertEqual(image.size,base.size)
        self.assertEqual(image.tobytes(),base.tobytes())
        self.assertTrue(equal_region(image,scale_box,scale))

    def test_modified_scale_and_missing_composition_are_detected(self):
        manifest=self.export();result=add(manifest,self.root/'composed',('group',))
        path=self.root/'composed/compositions-manifest.json'
        first=result['images'][0]
        with Image.open(first['path']) as original:
            image=original.copy()
        box=first['scale_box'];image.putpixel((box[0]+2,box[1]+2),(255,0,0));image.save(first['path'])
        with self.assertRaisesRegex(ValueError,'pixels or scale size'):
            verify(path)
        result['images'].pop();path.write_text(json.dumps(result))
        with self.assertRaisesRegex(ValueError,'exactly one'):
            verify(path)

    def test_bridge_uses_composed_images_and_keeps_raw_provenance(self):
        manifest_path=self.export();manifest=json.loads(manifest_path.read_text())
        composed=add(manifest_path,self.root/'composed',('group',))
        transcription={'records':[{'specimen_number':g['specimen_number'],'visually_checked':True,
            'identification_label_present':False,'collection_label_text':'SYNTHETIC LABEL','values':{}}
            for g in manifest['groups']]}
        result=join_records(manifest,transcription,'带比例尺分组',composed)
        first=result['records'][0]
        self.assertEqual(first['values']['图像'],'带比例尺分组/TEST-01.png')
        self.assertEqual(first['image_provenance']['group_image'],manifest['groups'][0]['path'])
        self.assertEqual(first['image_provenance']['scale_composition']['scale_factor'],1)
        wrong=copy.deepcopy(composed);wrong['source_sha256']='wrong'
        with self.assertRaisesRegex(ValueError,'different photograph'):
            join_records(manifest,transcription,'带比例尺分组',wrong)


if __name__=='__main__':
    unittest.main()
