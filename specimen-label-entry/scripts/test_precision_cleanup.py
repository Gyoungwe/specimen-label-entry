"""Regression checks for label remnants, thin appendages and ruler mask provenance."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
import numpy as np
from PIL import Image, ImageDraw
from crop_cleanup import apply, prepare, verify as verify_cleanup, boundary_audit
from photo_groups import digest, split
from photo_records import join_records
from precision_masks import clean_background, polygon_mask, paper_background
from scale_overlay import extract_ink,compose_overlay,extend_background
from shared_scale import add, extract, verify


def rectangle(x0,y0,x1,y1):
    return [[x0,y0],[x1,y0],[x1,y1],[x0,y1]]


def sample():
    image=Image.new('RGB',(200,160),(185,180,195))
    draw=ImageDraw.Draw(image)
    draw.ellipse((70,25,145,75),fill=(80,45,30))
    draw.line((90,70,85,120,80,147),fill=(90,65,40),width=2)
    draw.line((0,125,199,125),fill='black',width=4)
    plan={'source_sha256':'source','image_size':list(image.size),'coordinate_space':'crop_pixels','reviewed':True,
          'exclude_polygons':[rectangle(0,118,199,159)],
          'protected_polygons':[rectangle(60,15,155,85),[[86,75],[94,75],[88,125],[83,150],[77,149],[82,122]]],
          'background_samples':[[10,90,40,110],[140,90,180,110],[155,30,190,60]],
          'protection_margin':1,'feather_radius':4}
    return image,plan


class PrecisionMaskTests(unittest.TestCase):
    def test_border_removed_without_clipping_crossing_antenna_or_body(self):
        image,plan=sample();cleaned,checks=clean_background(image,plan,'source')
        self.assertEqual(cleaned.size,image.size)
        self.assertEqual(cleaned.getpixel((15,125)),(185,180,195))
        self.assertEqual(cleaned.getpixel((85,125)),image.getpixel((85,125)))
        self.assertEqual(cleaned.crop((60,15,155,85)).tobytes(),image.crop((60,15,155,85)).tobytes())
        self.assertTrue(checks['outside_mask_unchanged'])
        self.assertTrue(checks['protected_pixels_unchanged'])

    def test_unreviewed_wrong_source_wrong_size_and_preview_coordinates_rejected(self):
        image,plan=sample()
        for key,value in [('reviewed',False),('source_sha256','another'),('image_size',[100,80]),('coordinate_space','normalized_0_1000')]:
            with self.subTest(key=key):
                bad={**plan,key:value}
                with self.assertRaises(ValueError):clean_background(image,bad,'source')

    def test_invalid_polygon_coordinates_are_rejected(self):
        for point in [[200,0],[-1,0],[199.8,0],[float('nan'),0],[True,0]]:
            with self.subTest(point=point),self.assertRaises(ValueError):
                polygon_mask((200,160),[[point,[10,10],[20,20]]])

    def test_explicit_subject_protection_is_required(self):
        image,plan=sample();plan['protected_polygons']=[]
        with self.assertRaisesRegex(ValueError,'protected subject'):clean_background(image,plan,'source')

    def test_ruler_inclusion_mask_cannot_silently_mean_crop_cleanup(self):
        image,plan=sample();plan['include_polygons']=[rectangle(60,15,155,85)]
        with self.assertRaisesRegex(ValueError,'ruler-only'):clean_background(image,plan,'source')

    def test_entirely_protected_cleanup_is_rejected(self):
        image,plan=sample();plan['protected_polygons']=[rectangle(0,0,199,159)]
        with self.assertRaisesRegex(ValueError,'completely protected'):clean_background(image,plan,'source')

    def test_foreground_is_rejected_as_paper_sample(self):
        image,plan=sample();plan['background_samples']=[[0,118,40,132]]
        with self.assertRaisesRegex(ValueError,'foreground'):clean_background(image,plan,'source')

    def test_collinear_background_samples_do_not_extrapolate_colour(self):
        image=Image.new('RGB',(150,120),(180,175,195));draw=ImageDraw.Draw(image)
        draw.rectangle((10,80,25,95),fill=(182,177,197))
        draw.rectangle((60,82,75,97),fill=(179,174,194))
        draw.rectangle((110,84,125,99),fill=(180,175,195))
        paper=paper_background(image,[[10,80,25,95],[60,82,75,97],[110,84,125,99]])
        self.assertGreaterEqual(paper[:,:,:3].min(),174)
        self.assertLessEqual(paper[:,:,:3].max(),197)

    def test_audit_flags_dark_edges_without_erasing_them(self):
        image=Image.new('RGB',(100,80),'white');ImageDraw.Draw(image).line((0,0,0,79),fill='black',width=2)
        before=image.tobytes();self.assertIn('left',boundary_audit(image)['inspect_edges'])
        self.assertEqual(image.tobytes(),before)

    def test_ruler_removes_foreign_ink_but_preserves_source_rgb_and_tick_spacing(self):
        image=Image.new('RGB',(240,110),(190,185,200));draw=ImageDraw.Draw(image)
        draw.text((45,35),'10 mm',fill='black');draw.line((30,85,210,85),fill='black',width=3)
        draw.line((30,75,30,98),fill='black',width=3);draw.line((210,75,210,98),fill='black',width=3)
        draw.line((100,0,100,30),fill='black',width=4)
        plan={'source_sha256':'ruler','coordinate_space':'crop_pixels','image_size':list(image.size),'reviewed':True,
              'exclude_polygons':[rectangle(95,0,105,32)]}
        asset,meta=extract_ink(image,plan,'ruler')
        self.assertGreater(meta['removed_ink_pixels'],30)
        self.assertEqual(asset.convert('RGB').tobytes(),image.crop(meta['ink_box']).tobytes())
        restored=Image.new('L',image.size);restored.paste(asset.getchannel('A'),tuple(meta['ink_box'][:2]))
        self.assertEqual(restored.getpixel((100,10)),0)
        self.assertEqual(restored.getpixel((30,85)),255)
        self.assertEqual(restored.getpixel((210,85)),255)

    def test_extension_does_not_stretch_a_dark_label_fragment_into_stripes(self):
        base=Image.new('RGB',(320,180),(185,180,195));draw=ImageDraw.Draw(base)
        draw.rectangle((0,160,70,179),fill='black')
        asset=Image.new('RGBA',(220,90),(10,10,10,0))
        output,base_box,_=extend_background(base,asset)
        self.assertEqual(output.crop(base_box).tobytes(),base.tobytes())
        extension=np.asarray(output)[base.height:]
        self.assertEqual(int(extension[:,:,:3].min()),180)
        self.assertEqual(int(extension[:,:,:3].max()),195)

    def test_extension_preserves_narrow_base_in_rgb_rgba_and_grayscale(self):
        for mode,colour in [('RGB',(185,180,195)),('RGBA',(185,180,195,70)),('L',185)]:
            with self.subTest(mode=mode):
                base=Image.new(mode,(25,40),colour);asset=Image.new('RGBA',(100,60),(30,30,30,0))
                output,base_box,scale_box=extend_background(base,asset)
                self.assertEqual(output.mode,mode)
                self.assertEqual(output.crop(base_box).tobytes(),base.tobytes())
                self.assertEqual((scale_box[2]-scale_box[0],scale_box[3]-scale_box[1]),asset.size)


class CleanupWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        photo=Image.new('RGB',(600,420),(185,180,195));draw=ImageDraw.Draw(photo)
        body,_=sample();photo.paste(body,(280,20))
        draw.rectangle((20,20,230,220),outline='black',width=3)
        draw.rectangle((280,145,480,240),outline='black',width=3)
        draw.text((285,200),'TEST-1',fill='black')
        draw.text((70,330),'10 mm',fill='black');draw.line((40,375,240,375),fill='black',width=4)
        draw.line((130,285,130,310),fill='black',width=3)
        source=self.root/'photo.png';photo.save(source)
        def norm(box):return [box[0]/.6,box[1]/.42,box[2]/.6,box[3]/.42]
        plan={'source_sha256':digest(source),'display_size':list(photo.size),'coordinate_space':'normalized_0_1000',
              'groups':[{'specimen_number':'TEST-1','id_verified':True,'pairing_reviewed':True,
              'identification_label_present':False,'components':{'specimen':norm([280,20,480,180]),
              'catalogue':norm([280,145,480,240]),'collection':norm([20,20,230,220]),'scale':None}}],
              'shared_scale':{'box':norm([20,280,260,400]),'label_text':'10 mm','visually_checked':True}}
        path=self.root/'groups-plan.json';path.write_text(json.dumps(plan));split(source,path,self.root/'raw',padding=0)
        self.manifest=self.root/'raw/groups-manifest.json';self.raw=json.loads(self.manifest.read_text())
        crop=Path(self.raw['groups'][0]['components']['specimen']['path'])
        with Image.open(crop) as file:crop_size=list(file.size)
        _,entry=sample();entry.update({'specimen_number':'TEST-1','target':'specimen','source_sha256':digest(crop),
                                     'image_size':crop_size})
        self.plan=self.root/'cleanup-plan.json'
        self.plan.write_text(json.dumps({'source_manifest_sha256':digest(self.manifest),'entries':[entry]}))

    def tearDown(self):self.temp.cleanup()

    def cleanup(self):
        apply(self.manifest,self.plan,self.root/'cleaned')
        return self.root/'cleaned/cleanup-manifest.json'

    def test_guides_provide_native_size_hash_and_edge_audit(self):
        result=prepare(self.manifest,self.root/'guides')
        self.assertEqual(len(result['entries']),2)
        for entry in result['entries']:
            self.assertTrue(Path(entry['coordinate_guide']).is_file())
            self.assertEqual(entry['coordinate_space'],'crop_pixels')
            with Image.open(entry['coordinate_guide']) as file:
                self.assertEqual(file.size,tuple(entry['image_size']))
        ruler=result['shared_scale_review']
        self.assertEqual(ruler['coordinate_space'],'crop_pixels')
        self.assertTrue(Path(ruler['coordinate_guide']).is_file())

    def test_cleaned_copy_verifies_and_raw_crop_remains_unchanged(self):
        crop=Path(self.raw['groups'][0]['components']['specimen']['path']);before=digest(crop)
        path=self.cleanup();checks=verify_cleanup(path)
        self.assertEqual(checks['cleaned_images'],1);self.assertEqual(digest(crop),before)
        self.assertTrue(checks['protected_pixels_unchanged'])

    def test_modified_cleaned_pixels_are_detected(self):
        path=self.cleanup();entry=json.loads(path.read_text())['images'][0]
        with Image.open(entry['path']) as file:image=file.copy()
        image.putpixel((0,0),(255,0,0));image.save(entry['path'])
        with self.assertRaisesRegex(ValueError,'differs'):verify_cleanup(path)

    def test_wrong_manifest_duplicate_target_and_wrong_source_fail_before_output(self):
        plan=json.loads(self.plan.read_text())
        for variant in ['source','duplicate','image']:
            with self.subTest(variant=variant):
                bad=copy.deepcopy(plan)
                if variant=='source':bad['source_manifest_sha256']='wrong'
                if variant=='duplicate':bad['entries']*=2
                if variant=='image':bad['entries'][0]['source_sha256']='wrong'
                self.plan.write_text(json.dumps(bad))
                with self.assertRaises(ValueError):apply(self.manifest,self.plan,self.root/'invalid')
                self.assertFalse((self.root/'invalid').exists())

    def test_cleaned_base_and_precision_ruler_compose_and_verify_together(self):
        cleanup=self.cleanup();scale=Path(self.raw['shared_scale']['path'])
        with Image.open(scale) as file:scale_size=list(file.size)
        mask={'source_sha256':digest(scale),'coordinate_space':'crop_pixels','image_size':scale_size,
              'reviewed':True,'exclude_polygons':[rectangle(105,0,115,32)]}
        path=self.root/'scale-mask.json';path.write_text(json.dumps(mask))
        preview=extract(self.manifest,self.root/'preview',path)
        self.assertTrue(Path(preview['preview']).is_file())
        result=add(self.manifest,self.root/'composed',scale_mask=path,cleanup=cleanup)
        self.assertEqual(len(result['images']),2)
        checks=verify(self.root/'composed/compositions-manifest.json')
        self.assertEqual(checks['cleaned_base_images'],1);self.assertEqual(checks['scale_factor'],1)
        records=join_records(self.raw,{'records':[{'specimen_number':'TEST-1','visually_checked':True,
            'identification_label_present':False,'collection_label_text':'SYNTHETIC LABEL','values':{}}]},composition=result)
        provenance=records['records'][0]['image_provenance']
        self.assertEqual(provenance['group_image'],self.raw['groups'][0]['path'])
        self.assertEqual(provenance['scale_composition']['cleanup_manifest'],str(cleanup.resolve()))
        saved=json.loads(cleanup.read_text());saved['images'][0]['plan']['reviewed']=False
        cleanup.write_text(json.dumps(saved))
        with self.assertRaisesRegex(ValueError,'Cleanup manifest changed'):verify(self.root/'composed/compositions-manifest.json')

    def test_archived_unmasked_overlay_manifest_still_verifies(self):
        result=add(self.manifest,self.root/'old')
        for key in ['algorithm_version','precision_mask','removed_ink_pixels','retained_source_rgb_unchanged']:
            del result['scale_cutout'][key]
        del result['composition_algorithm_version']
        with Image.open(self.raw['shared_scale']['path']) as file:ink,_=extract_ink(file,algorithm_version=1)
        for entry in result['images']:
            with Image.open(entry['base_path']) as file:base=file.copy()
            image,box,scale_box,placement=compose_overlay(base,ink,self.raw['groups'][0],entry['target'],self.raw['shared_scale'],background_version=1)
            image.save(entry['path']);entry.update({'base_box':box,'scale_box':scale_box,'placement':placement})
        path=self.root/'old/compositions-manifest.json';path.write_text(json.dumps(result))
        self.assertEqual(verify(path)['composed_images'],2)


if __name__=='__main__':unittest.main()
