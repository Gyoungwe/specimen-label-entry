"""Check flexible counts, transparent ruler geometry and protected evidence."""
import json
import math
from pathlib import Path
import tempfile
import unittest
from PIL import Image,ImageDraw
from photo_groups import digest,split,validate_plan
from scale_overlay import extract_ink,compose_overlay,protected_unchanged,overlaps,protected_boxes
from shared_scale import add,verify


def arbitrary_fixture(root,count):
    cols=min(8,math.ceil(math.sqrt(count)));rows=math.ceil(count/cols)
    width,height=cols*240,rows*180+110
    image=Image.new('RGB',(width,height),(185,179,193));draw=ImageDraw.Draw(image)
    def normalized(box):return [box[0]*1000/width,box[1]*1000/height,box[2]*1000/width,box[3]*1000/height]
    plan={'coordinate_space':'normalized_0_1000','display_size':[width,height],
          'expected_groups':count,'groups':[]}
    for index in range(count):
        x=(index%cols)*240;y=(index//cols)*180
        boxes={'specimen':[x+140,y+20,x+200,y+65],
               'collection':[x+15,y+15,x+115,y+110],
               'catalogue':[x+130,y+125,x+220,y+160]}
        for role,box in boxes.items():
            if role=='specimen':draw.ellipse(box,fill=(70,40,30))
            else:draw.rectangle(box,outline='black',width=2)
        plan['groups'].append({'specimen_number':f'TEST-{index+1:04d}','id_verified':True,
            'pairing_reviewed':True,'identification_label_present':False,
            'components':{**{k:normalized(v) for k,v in boxes.items()},'identification':None,'scale':None}})
    scale=[20,rows*180+15,220,rows*180+95]
    draw.rectangle(scale,fill=(202,199,208))
    draw.text((40,scale[1]+10),'10 mm',fill=(20,20,20))
    draw.line((40,scale[3]-20,200,scale[3]-20),fill=(20,20,20),width=4)
    source=root/'source.png';image.save(source)
    plan['source_sha256']=digest(source)
    plan['shared_scale']={'box':normalized(scale),'label_text':'10 mm','visually_checked':True}
    return source,plan,image


class ScaleOverlayTests(unittest.TestCase):
    def test_counts_one_four_twelve_and_thirty_seven_end_to_end(self):
        for count in (1,4,12,37):
            with self.subTest(count=count),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);source,plan,_=arbitrary_fixture(root,count)
                path=root/'plan.json';path.write_text(json.dumps(plan))
                split(source,path,root/'raw',padding=0)
                result=add(root/'raw/groups-manifest.json',root/'composed',('group',))
                self.assertEqual(len(result['images']),count)
                self.assertEqual(len({e['specimen_number'] for e in result['images']}),count)
                self.assertEqual(verify(root/'composed/compositions-manifest.json')['groups'],count)

    def test_no_fixed_count_cap_and_optional_expected_count(self):
        with tempfile.TemporaryDirectory() as temp:
            _,plan,image=arbitrary_fixture(Path(temp),128)
            del plan['expected_groups']
            self.assertEqual(len(validate_plan(plan,image)),128)

    def test_cutout_preserves_source_rgb_and_pixel_geometry(self):
        with tempfile.TemporaryDirectory() as temp:
            _,_,image=arbitrary_fixture(Path(temp),1)
            source=image.crop((20,195,220,275))
            asset,meta=extract_ink(source)
            self.assertEqual(asset.convert('RGB').tobytes(),source.crop(meta['ink_box']).tobytes())
            self.assertEqual(asset.size,(meta['ink_box'][2]-meta['ink_box'][0],meta['ink_box'][3]-meta['ink_box'][1]))
            self.assertEqual(asset.getchannel('A').getextrema()[0],0)

    def test_overlay_does_not_cover_specimen_or_labels(self):
        base=Image.new('RGB',(600,450),(190,180,200));draw=ImageDraw.Draw(base)
        group={'group_box':[0,0,600,450],'components':{
            'specimen':{'box':[300,180,450,300]},'catalogue':{'box':[280,340,500,420]},
            'collection':{'box':[15,20,240,330]}}}
        for box in protected_boxes(group,'group',base.size,0):draw.rectangle(box,fill=(80,50,40))
        asset=Image.new('RGBA',(100,60),(30,30,30,0));ImageDraw.Draw(asset).line((10,30,90,30),fill='black',width=3)
        shared={'box':[800,800,900,850]}
        image,base_box,scale_box,placement=compose_overlay(base,asset,group,'group',shared)
        self.assertEqual(placement,'transparent_overlay')
        self.assertEqual(image.size,base.size)
        self.assertTrue(protected_unchanged(base,image,group,'group',base_box))
        self.assertFalse(any(overlaps(scale_box,b) for b in protected_boxes(group,'group',base.size)))

    def test_no_space_extends_matching_background_and_keeps_base(self):
        base=Image.new('RGB',(80,80),(180,175,195));asset=Image.new('RGBA',(130,70),(25,25,25,0))
        ImageDraw.Draw(asset).line((5,55,125,55),fill='black',width=3)
        group={'group_box':[0,0,80,80],'components':{}}
        image,base_box,scale_box,placement=compose_overlay(base,asset,group,'specimen',{'box':[100,100,200,200]})
        self.assertEqual(placement,'matched_background_extension')
        self.assertEqual(image.crop(base_box).tobytes(),base.tobytes())
        self.assertEqual((scale_box[2]-scale_box[0],scale_box[3]-scale_box[1]),asset.size)
        self.assertLess(sum(abs(a-b) for a,b in zip(image.getpixel((0,image.height-1)),(180,175,195))),12)

    def test_footer_manifests_remain_verifiable(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source,plan,_=arbitrary_fixture(root,1)
            path=root/'plan.json';path.write_text(json.dumps(plan))
            split(source,path,root/'raw',padding=0)
            result=add(root/'raw/groups-manifest.json',root/'footer',('group',),style='footer')
            result.pop('style');result['schema_version']=1
            path=root/'footer/compositions-manifest.json';path.write_text(json.dumps(result))
            self.assertEqual(verify(path)['style'],'footer')


if __name__=='__main__':unittest.main()
