#!/usr/bin/env python3
"""Regression checks for crop integrity, group mixing and record coverage."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from PIL import Image
from photo_groups import digest, open_source, pixel_box, split, validate_plan, verify
from photo_records import join_records


class PhotoGroupsTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.source=self.root/'source.png'
        image=Image.new('RGBA',(200,100),(190,180,170,255))
        image.putpixel((20,20),(10,20,30,0))
        image.save(self.source)
        self.image=image
        self.plan={'source_sha256':digest(self.source),'display_size':[200,100],
                   'coordinate_space':'normalized_0_1000','expected_groups':2,
                   'groups':[{'specimen_number':sid,'id_verified':True,'pairing_reviewed':True,
                              'identification_label_present':False,
                              'components':{'specimen':[x,100,x+100,300],
                                            'catalogue':[x,400,x+100,500],
                                            'collection':[x+120,100,x+200,500],
                                            'identification':None,'scale':None}}
                             for sid,x in [('IOZ(E)001',50),('IOZ(E)002',600)]]}

    def tearDown(self):
        self.temp.cleanup()

    def export(self,plan=None):
        path=self.root/'plan.json'
        path.write_text(json.dumps(plan or self.plan))
        return split(self.source,path,self.root/'crops')

    def test_box_rounding_and_invalid_values(self):
        self.assertEqual(pixel_box([1,1,999,999],101,51),[0,0,101,51])
        for box in ([0,0,1001,500],[5,0,4,9],[True,0,8,9],[0,0,float('nan'),9]):
            with self.subTest(box=box),self.assertRaises(ValueError):
                pixel_box(box,100,100)

    def test_duplicate_ids_are_rejected(self):
        self.plan['groups'][1]['specimen_number']=self.plan['groups'][0]['specimen_number']
        with self.assertRaisesRegex(ValueError,'Duplicate'):
            validate_plan(self.plan,self.image)

    def test_explicit_outer_box_cannot_clip_components(self):
        self.plan['groups'][0]['group_box']=[50,100,200,200]
        with self.assertRaisesRegex(ValueError,'clips'):
            validate_plan(self.plan,self.image)

    def test_source_fingerprint_is_required(self):
        self.plan['source_sha256']='wrong'
        with self.assertRaisesRegex(ValueError,'SHA-256'):
            self.export()

    def test_foreign_components_inside_group_are_rejected(self):
        self.plan['groups'][0]['group_box']=[0,0,800,800]
        with self.assertRaisesRegex(ValueError,'intersects'):
            self.export()

    def test_pixel_integrity_and_rgba_tamper_detection(self):
        manifest=self.export()
        self.assertEqual(verify(self.root/'crops/groups-manifest.json')['pixel_identical_crops'],8)
        crop_path=Path(manifest['groups'][0]['path'])
        with Image.open(crop_path) as crop:
            altered=crop.copy()
        region=manifest['groups'][0]['group_box']
        altered.putpixel((20-region[0],20-region[1]),(1,2,3,0))
        altered.save(crop_path)
        with self.assertRaisesRegex(ValueError,'Pixels differ'):
            verify(self.root/'crops/groups-manifest.json')

    def test_exif_transpose_dimensions(self):
        source=self.root/'oriented.jpg'
        exif=Image.Exif();exif[274]=6
        Image.new('RGB',(120,80),(10,20,30)).save(source,exif=exif)
        image,orientation=open_source(source)
        self.assertEqual((image.size,orientation),((80,120),6))

    def test_identification_presence_matches_component(self):
        self.plan['groups'][0]['identification_label_present']=True
        with self.assertRaisesRegex(ValueError,'presence'):
            validate_plan(self.plan,self.image)

    def test_record_coverage_and_relative_image_reference(self):
        manifest=self.export()
        records={'records':[{'specimen_number':g['specimen_number'],'visually_checked':True,
                             'identification_label_present':False,'collection_label_text':'中国，采集标签',
                             'values':{}} for g in manifest['groups']]}
        batch=join_records(manifest,records)
        self.assertEqual(len(batch['records']),2)
        self.assertEqual(batch['records'][0]['values']['图像'],'原像素分组/IOZ(E)001.png')
        incomplete=copy.deepcopy(records);incomplete['records'].pop()
        with self.assertRaisesRegex(ValueError,'exactly once'):
            join_records(manifest,incomplete)
        duplicate=copy.deepcopy(records);duplicate['records'][1]=duplicate['records'][0]
        with self.assertRaisesRegex(ValueError,'exactly once'):
            join_records(manifest,duplicate)
        records['records'][0]['values']['标本号']='another-number'
        with self.assertRaisesRegex(ValueError,'differs'):
            join_records(manifest,records)

    def test_pairing_review_is_required_before_records(self):
        manifest=self.export();manifest['groups'][0]['pairing_reviewed']=False
        records={'records':[{'specimen_number':g['specimen_number'],'visually_checked':True,
                             'identification_label_present':False,'collection_label_text':'中国'}
                            for g in manifest['groups']]}
        with self.assertRaisesRegex(ValueError,'pairing'):
            join_records(manifest,records)


if __name__=='__main__':
    unittest.main()
