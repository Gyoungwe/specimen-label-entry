"""Source-based retries, reviewed direct extraction and named image delivery."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from PIL import Image,ImageDraw
from photo_groups import digest,split,verify as verify_crops
from photo_records import join_records
from photo_review import inventory,number_candidates,cards,source_views,verify as verify_review
from photo_delivery import export,verify as verify_delivery
from shared_scale import add
from scale_overlay import protected_boxes


def fixture(root):
    source=root/'photo.png';image=Image.new('RGB',(400,200),(185,180,195));draw=ImageDraw.Draw(image)
    groups=[]
    for index,x in enumerate([100,600],1):
        components={'specimen':[x,100,x+100,300],'catalogue':[x,350,x+100,450],
                    'collection':[x+120,100,x+320,450]}
        for role,box in components.items():
            pixels=[round(box[0]*.4),round(box[1]*.2),round(box[2]*.4),round(box[3]*.2)]
            draw.rectangle(pixels,outline='black',width=2)
        groups.append({'specimen_number':f'IOZ(E){index:07d}','id_verified':True,'pairing_reviewed':True,
                       'identification_label_present':False,'components':components})
    draw.text((25,165),'10 mm',fill='black');draw.line((20,190,155,190),fill='black',width=3)
    image.save(source)
    plan={'source_sha256':digest(source),'display_size':list(image.size),'coordinate_space':'normalized_0_1000',
          'groups':groups,'shared_scale':{'box':[25,800,420,980],'label_text':'10 mm','visually_checked':True}}
    return source,plan,image


class RetryWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.source,self.plan,self.image=fixture(self.root)
        self.plan_path=self.root/'plan.json';self.plan_path.write_text(json.dumps(self.plan))
        self.manifest_path=self.root/'raw/groups-manifest.json'

    def raw(self):return split(self.source,self.plan_path,self.root/'raw',padding=0)

    def records(self):
        manifest=self.raw()
        trans={'records':[{'specimen_number':g['specimen_number'],'visually_checked':True,
               'identification_label_present':False,'collection_label_text':'Synthetic collection card','values':{}}
               for g in manifest['groups']]}
        batch=join_records(manifest,trans);path=self.root/'records.json';path.write_text(json.dumps(batch));return path,batch

    def test_prefix_variants_are_candidates_without_inventing_missing_numbers(self):
        for prefix in ['IOZ(E)','TOZ(E)','10Z(E)','1OZ(E)']:
            self.assertEqual(number_candidates(prefix+'2833511',7,'2833'),['2833511'])
        self.assertEqual(number_candidates('2833501-2833504',7,'2833'),['2833501','2833504'])
        self.assertEqual(number_candidates('128335011 28335O1 0.2833511',7,'2833'),[])

    def test_inventory_keeps_zero_ocr_photos_and_flags_range_candidates(self):
        ocr=self.root/'ocr.json';ocr.write_text(json.dumps([{'image':'photo.JPG','lines':[{'text':'TOZ(E)2833501-2833504'}]}]))
        result=inventory([self.source],self.root/'inventory.json',ocr,7,'2833')
        entry=result['photos'][0]
        self.assertEqual(entry['classification'],'unreviewed')
        self.assertEqual(entry['ocr_candidates'][0]['kind'],'range_or_listing')
        self.assertFalse(entry['ocr_candidates'][0]['visually_verified'])
        result=inventory([self.source],self.root/'no-ocr.json')
        self.assertEqual(len(result['photos']),1);self.assertEqual(result['photos'][0]['ocr_candidates'],[])

    def test_same_basename_does_not_assign_ocr_to_another_photo(self):
        folder=self.root/'other';folder.mkdir();other=folder/'photo.png';self.image.save(other)
        ocr=self.root/'ocr.json';ocr.write_text(json.dumps([{'image':'somewhere/photo.jpg','lines':[{'text':'IOZ(E)2833501'}]}]))
        result=inventory([self.source,other],self.root/'inventory.json',ocr)
        self.assertTrue(all(p['ocr_name_ambiguous'] and not p['ocr_candidates'] for p in result['photos']))

    def test_expanded_card_uses_width_for_x_height_for_y_and_keeps_native_evidence(self):
        self.raw();sid=self.plan['groups'][0]['specimen_number']
        result=cards(self.manifest_path,self.root/'cards',[sid]);entry=result['views'][0]
        self.assertEqual(entry['source_box'],[64,8,192,102])
        self.assertEqual(entry['preview_size'],[256,188])
        self.assertEqual(result['padding_coordinate_space'],'normalized_0_1000')
        with Image.open(entry['native_path']) as actual:self.assertEqual(actual.tobytes(),self.image.crop(entry['source_box']).tobytes())
        self.assertEqual(verify_review(self.root/'cards/review-manifest.json')['native_views_verified'],1)

    def test_missing_card_box_switches_to_source_review_instead_of_guessing(self):
        self.plan['groups'][0]['components']['collection']=None;self.plan_path.write_text(json.dumps(self.plan));self.raw()
        with self.assertRaisesRegex(ValueError,'source views'):cards(self.manifest_path,self.root/'cards',[self.plan['groups'][0]['specimen_number']])
        self.assertFalse((self.root/'cards').exists())

    def test_explicit_whole_group_enlargement_works_without_collection_component(self):
        self.plan['groups'][0]['components']['collection']=None;self.plan_path.write_text(json.dumps(self.plan));self.raw()
        result=cards(self.manifest_path,self.root/'groups-review',[self.plan['groups'][0]['specimen_number']],region='group')
        self.assertEqual(result['purpose'],'expanded_group_review')
        self.assertEqual(result['views'][0]['region'],'group')
        self.assertEqual(verify_review(self.root/'groups-review/review-manifest.json')['native_views_verified'],1)

    def test_unannotated_halves_bands_and_whole_cover_source_without_gaps(self):
        for layout in ['whole','halves','bands']:
            with self.subTest(layout=layout):
                result=source_views(self.source,self.root/layout,layout)
                area=sum((e['source_box'][2]-e['source_box'][0])*(e['source_box'][3]-e['source_box'][1]) for e in result['views'])
                self.assertEqual(area,self.image.width*self.image.height)
                self.assertFalse(result['annotations'])
                self.assertEqual(verify_review(self.root/layout/'review-manifest.json')['native_views_verified'],len(result['views']))

    def test_review_detects_modified_pixels_and_changed_source(self):
        result=source_views(self.source,self.root/'views');path=Path(result['views'][0]['native_path'])
        with Image.open(path) as file:image=file.copy()
        image.putpixel((0,0),(255,0,0));image.save(path)
        with self.assertRaisesRegex(ValueError,'pixels changed'):verify_review(self.root/'views/review-manifest.json')
        self.image.putpixel((0,0),(1,2,3));self.image.save(self.source)
        with self.assertRaisesRegex(ValueError,'SHA-256'):verify_review(self.root/'views/review-manifest.json')

    def test_reading_rotation_does_not_rotate_native_evidence_or_its_coordinates(self):
        self.image.putpixel((399,0),(10,20,30));self.image.save(self.source)
        result=source_views(self.source,self.root/'turned','whole',rotation=90);entry=result['views'][0]
        self.assertEqual(entry['source_box'],[0,0,400,200]);self.assertEqual(entry['preview_size'],[200,400])
        with Image.open(entry['native_path']) as native:self.assertEqual(native.tobytes(),self.image.tobytes())
        with Image.open(entry['preview_path']) as preview:self.assertEqual(preview.getpixel((0,0)),(10,20,30))
        self.assertEqual(verify_review(self.root/'turned/review-manifest.json')['native_views_verified'],1)

    def test_direct_fallback_records_overlap_and_retains_exact_source_pixels(self):
        self.plan['groups'][0]['group_box']=[0,0,900,650]
        self.plan['groups'][1]['group_box']=[550,0,1000,650]
        self.plan_path.write_text(json.dumps(self.plan))
        with self.assertRaisesRegex(ValueError,'intersects'):split(self.source,self.plan_path,self.root/'strict')
        self.plan['direct_review']={'visually_checked':True,'reason':'Crowded source reviewed directly'}
        self.plan_path.write_text(json.dumps(self.plan));result=split(self.source,self.plan_path,self.root/'direct',direct=True)
        self.assertTrue(result['overlap_warnings']);self.assertEqual(result['crop_method'],'reviewed_direct')
        self.assertEqual(verify_crops(self.root/'direct/groups-manifest.json')['groups'],2)
        first=result['groups'][0]
        with Image.open(first['path']) as actual:self.assertEqual(actual.tobytes(),self.image.crop(first['group_box']).tobytes())
        result['overlap_warnings']=[];(self.root/'direct/groups-manifest.json').write_text(json.dumps(result))
        with self.assertRaisesRegex(ValueError,'overlap evidence'):verify_crops(self.root/'direct/groups-manifest.json')

    def test_direct_fallback_accepts_whole_groups_without_component_boxes(self):
        for index,g in enumerate(self.plan['groups']):
            g.pop('components');g['group_box']=[index*400,0,700+index*300,650]
        self.plan['direct_review']={'visually_checked':True,'reason':'No further component-box retries'}
        self.plan_path.write_text(json.dumps(self.plan));result=split(self.source,self.plan_path,self.root/'direct',direct=True)
        self.assertEqual(result['groups'][0]['components'],{})
        g=result['groups'][0];self.assertEqual(protected_boxes(g,'group',(280,130)),[[0,0,280,130]])
        with self.assertRaisesRegex(ValueError,'--targets group'):add(self.root/'direct/groups-manifest.json',self.root/'bad')
        composed=add(self.root/'direct/groups-manifest.json',self.root/'composed',targets=('group',))
        self.assertEqual(len(composed['images']),2)

    def test_direct_fallback_requires_review_reason_and_verified_id(self):
        for g in self.plan['groups']:g['group_box']=[0,0,1000,650]
        self.plan_path.write_text(json.dumps(self.plan))
        with self.assertRaisesRegex(ValueError,'reviewed source'):split(self.source,self.plan_path,self.root/'bad',direct=True)
        self.plan['direct_review']={'visually_checked':True,'reason':'Source inspected'}
        self.plan['groups'][0]['id_verified']=False;self.plan_path.write_text(json.dumps(self.plan))
        with self.assertRaisesRegex(ValueError,'inspect the actual number'):split(self.source,self.plan_path,self.root/'bad',direct=True)

    def test_delivery_names_change_without_changing_ids_values_or_image_bytes(self):
        path,batch=self.records();before=digest(path)
        proof=export(path,self.root/'图像',self.root/'delivery-records.json')
        delivered=json.loads((self.root/'delivery-records.json').read_text())
        self.assertEqual(delivered['records'][0]['values']['标本号'],'IOZ(E)0000001')
        self.assertEqual(delivered['records'][0]['values']['图像'],'图像/0000001-1.png')
        self.assertEqual(digest(path),before)
        self.assertEqual(verify_delivery(self.root/'图像/delivery-manifest.json')['verified_images'],2)
        self.assertEqual(digest(Path(proof['images'][0]['path'])),digest(Path(proof['images'][0]['source_path'])))

    def test_delivery_detects_duplicate_ids_filename_collisions_and_extra_images(self):
        path,batch=self.records();bad=deepcopy(batch);bad['records'][1]['values']['标本号']='0000001';path.write_text(json.dumps(bad))
        with self.assertRaisesRegex(ValueError,'collisions'):export(path,self.root/'bad',self.root/'bad.json')
        self.assertFalse((self.root/'bad').exists());path.write_text(json.dumps(batch))
        export(path,self.root/'图像',self.root/'delivery-records.json');self.image.save(self.root/'图像/extra.png')
        with self.assertRaisesRegex(ValueError,'PNG count'):verify_delivery(self.root/'图像/delivery-manifest.json')

    def test_delivery_rejects_wrong_height_provenance_and_enlarged_review_crops(self):
        path,batch=self.records();bad=deepcopy(batch);bad['records'][0]['image_provenance']['group_box'][3]*=2
        path.write_text(json.dumps(bad))
        with self.assertRaisesRegex(ValueError,'original-pixel'):export(path,self.root/'bad',self.root/'bad.json')
        p=Path(batch['records'][0]['image_provenance']['group_image'])
        with Image.open(p) as file:large=file.resize((file.width*2,file.height*2))
        large.save(self.root/'large.png');batch['records'][0]['image_provenance']['group_image']=str(self.root/'large.png')
        path.write_text(json.dumps(batch))
        with self.assertRaisesRegex(ValueError,'original-pixel'):export(path,self.root/'large',self.root/'large.json')

    def test_composed_delivery_requires_and_verifies_composition_manifest(self):
        path,batch=self.records();composition=add(self.manifest_path,self.root/'composed',targets=('group',))
        by_id={e['specimen_number']:e for e in composition['images']}
        for r in batch['records']:r['image_provenance']['scale_composition']={'path':by_id[r['values']['标本号']]['path']}
        path.write_text(json.dumps(batch))
        with self.assertRaisesRegex(ValueError,'composition manifest'):export(path,self.root/'bad',self.root/'bad.json')
        export(path,self.root/'图像',self.root/'delivery-records.json',[self.root/'composed/compositions-manifest.json'])
        self.assertEqual(verify_delivery(self.root/'图像/delivery-manifest.json')['verified_images'],2)


if __name__=='__main__':unittest.main()
