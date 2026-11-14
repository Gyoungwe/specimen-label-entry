#!/usr/bin/env python3
"""Prepare multi-specimen photographs and export pixel-preserving catalogue groups."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
from PIL import Image, ImageDraw, ImageFont, ImageOps

ROLES=('specimen','catalogue','collection','identification','scale')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def open_source(path):
    with Image.open(path) as source:
        orientation=source.getexif().get(274,1)
        image=ImageOps.exif_transpose(source)
        if image.mode not in ('RGB','RGBA','L'):
            raise ValueError('Convert unusual colour modes explicitly before scientific cropping')
        return image.copy(),orientation


def pixel_box(box,width,height):
    if not isinstance(box,list) or len(box)!=4 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in box):
        raise ValueError('A box must be [left,top,right,bottom] in normalized_0_1000 coordinates')
    left,top,right,bottom=box
    if not (0<=left<right<=1000 and 0<=top<bottom<=1000):
        raise ValueError(f'Invalid normalized box: {box}')
    return [math.floor(left*width/1000),math.floor(top*height/1000),math.ceil(right*width/1000),math.ceil(bottom*height/1000)]


def intersection(a,b):
    return max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))


def validate_plan(plan,image):
    if plan.get('coordinate_space')!='normalized_0_1000':
        raise ValueError('Plan must explicitly use normalized_0_1000 coordinates')
    groups=plan.get('groups',[])
    expected=plan.get('expected_groups',len(groups))
    if not groups or isinstance(expected,bool) or not isinstance(expected,int) or expected!=len(groups):
        raise ValueError('Group count is empty or differs from expected_groups')
    shared=plan.get('shared_scale')
    if shared is not None:
        if not isinstance(shared,dict) or shared.get('visually_checked') is not True:
            raise ValueError('Inspect the common scale before setting shared_scale.visually_checked')
        pixel_box(shared.get('box'),*image.size)
        if not isinstance(shared.get('label_text'),str) or not shared['label_text'].strip():
            raise ValueError('Transcribe the shared scale label, including its unit')
    seen=set()
    for group in groups:
        sid=group.get('specimen_number','')
        if not isinstance(sid,str) or not re.fullmatch(r'[A-Za-z0-9()._-]+',sid):
            raise ValueError('Use a visually read catalogue ID with safe filename characters')
        if sid in seen:
            raise ValueError(f'Duplicate catalogue ID: {sid}')
        seen.add(sid)
        if group.get('id_verified') is not True:
            raise ValueError(f'{sid}: inspect the actual number label before setting id_verified')
        components=group.get('components',{})
        if shared is not None and components.get('scale') is not None:
            raise ValueError('Use shared_scale once at photo level; set each group scale to null')
        if set(components)-set(ROLES):
            raise ValueError('Unknown component role')
        if components.get('catalogue') is None or components.get('specimen') is None:
            raise ValueError(f'{sid}: specimen and catalogue boxes are required')
        for box in components.values():
            if box is not None:
                pixel_box(box,*image.size)
                if shared is not None and intersection(pixel_box(shared['box'],*image.size),pixel_box(box,*image.size)):
                    raise ValueError(f'Shared scale box overlaps {sid}; inspect the scale crop')
        if bool(components.get('identification'))!=bool(group.get('identification_label_present')):
            raise ValueError(f'{sid}: identification presence and component must agree')
        if group.get('group_box') is not None:
            outer=pixel_box(group['group_box'],*image.size)
            for box in components.values():
                if box is None:
                    continue
                inner=pixel_box(box,*image.size)
                if not (outer[0]<=inner[0] and outer[1]<=inner[1] and inner[2]<=outer[2] and inner[3]<=outer[3]):
                    raise ValueError(f'{sid}: group_box clips a declared component')
    return groups


def group_box(group,width,height,padding):
    if group.get('group_box') is not None:
        return pixel_box(group['group_box'],width,height)
    boxes=[pixel_box(box,width,height) for box in group['components'].values() if box is not None]
    px=math.ceil(width*padding/1000)
    py=math.ceil(height*padding/1000)
    return [max(0,min(b[0] for b in boxes)-px),max(0,min(b[1] for b in boxes)-py),
            min(width,max(b[2] for b in boxes)+px),min(height,max(b[3] for b in boxes)+py)]


def prepare(source,work):
    image,orientation=open_source(source)
    work.mkdir(parents=True,exist_ok=True)
    if (work/'photo-source.json').exists():
        raise ValueError('Choose a fresh prepare directory')
    preview=image.copy().convert('RGB')
    preview.thumbnail((3000,3000))
    preview.save(work/'overview.jpg',quality=93)
    grid=preview.copy()
    draw=ImageDraw.Draw(grid)
    font=ImageFont.load_default(size=20)
    for n in range(0,1001,100):
        x=round(n*grid.width/1000)
        y=round(n*grid.height/1000)
        draw.line((x,0,x,grid.height),fill='#32A1CB',width=1)
        draw.line((0,y,grid.width,y),fill='#32A1CB',width=1)
        draw.text((min(x+3,grid.width-55),3),str(n),font=font,fill='#A00000',stroke_width=1,stroke_fill='white')
        draw.text((3,min(y+3,grid.height-30)),str(n),font=font,fill='#A00000',stroke_width=1,stroke_fill='white')
    grid.save(work/'overview-grid.jpg',quality=94)
    metadata={'path':str(source.resolve()),'sha256':digest(source),'display_size':list(image.size),
              'exif_orientation':orientation,'coordinate_space':'normalized_0_1000',
              'overview':str((work/'overview.jpg').resolve()),'coordinate_grid':str((work/'overview-grid.jpg').resolve())}
    (work/'photo-source.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2))
    print(json.dumps(metadata,ensure_ascii=False))


def split(source,plan_path,out,padding=12):
    if out.exists():
        raise ValueError('Choose a new output directory; existing crops are not overwritten')
    image,orientation=open_source(source)
    plan=json.loads(plan_path.read_text())
    before=digest(source)
    if plan.get('source_sha256')!=before:
        raise ValueError('Source SHA-256 mismatch; re-inspect this photograph')
    if plan.get('display_size')!=list(image.size):
        raise ValueError('Display dimensions differ; coordinates may refer to a resized screenshot')
    groups=validate_plan(plan,image)
    regions=[group_box(g,*image.size,padding) for g in groups]
    for i,region in enumerate(regions):
        for j,other in enumerate(groups):
            if i==j:
                continue
            for role,box in other['components'].items():
                if box is not None and intersection(region,pixel_box(box,*image.size)):
                    raise ValueError(f"{groups[i]['specimen_number']} crop intersects {other['specimen_number']}:{role}; revise the boxes/padding")
    out.mkdir(parents=True)
    details=out/'components'
    details.mkdir()
    manifest={'schema_version':3,'skill_version':'3.4.0','expected_groups':len(groups),'source':{'path':str(source.resolve()),'name':source.name,'sha256':before,
               'display_size':list(image.size),'exif_orientation':orientation},'coordinate_space':'normalized_0_1000',
               'processing':'EXIF orientation, integer rectangular crop, lossless PNG; no resampling or colour correction',
               'human_review_pending':True,'groups':[]}
    if plan.get('shared_scale') is not None:
        scale=plan['shared_scale']
        actual=pixel_box(scale['box'],*image.size)
        scale_path=out/'shared-scale.png'
        image.crop(actual).save(scale_path,format='PNG',compress_level=6)
        manifest['shared_scale']={'box':actual,'path':str(scale_path.resolve()),'label_text':scale['label_text'],
            'visually_checked':True,'replication':'unresized source pixels, same photograph only',
            'calibration_status':'shared_reference_requires_plane_and_perspective_review'}
    for group,region in zip(groups,regions):
        sid=group['specimen_number']
        output=out/(sid+'.png')
        crop=image.crop(region)
        crop.save(output,format='PNG',compress_level=6)
        entries={}
        for role,box in group['components'].items():
            if box is None:
                entries[role]=None
                continue
            actual=pixel_box(box,*image.size)
            component_path=details/(sid+'__'+role+'.png')
            image.crop(actual).save(component_path,format='PNG',compress_level=6)
            entries[role]={'box':actual,'path':str(component_path.resolve())}
        manifest['groups'].append({'specimen_number':sid,'group_box':region,'path':str(output.resolve()),
            'components':entries,'identification_label_present':bool(group.get('identification_label_present')),
            'id_verified':True,'pairing_reviewed':bool(group.get('pairing_reviewed')),
            'collection_label_text':group.get('collection_label_text'),
            'issues':group.get('issues',[]),'pixel_sha256':hashlib.sha256(crop.tobytes()).hexdigest()})
    if digest(source)!=before:
        raise ValueError('Source changed during processing')
    (out/'groups-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    verify(out/'groups-manifest.json')
    # Labelled overview is a review derivative; evidence crops above remain unannotated.
    overview=image.copy().convert('RGB')
    overview.thumbnail((2400,2400))
    draw=ImageDraw.Draw(overview)
    font=ImageFont.load_default(size=22)
    for group,box in zip(groups,regions):
        scaled=[round(box[0]*overview.width/image.width),round(box[1]*overview.height/image.height),
                round(box[2]*overview.width/image.width),round(box[3]*overview.height/image.height)]
        draw.rectangle(scaled,outline='#D33E26',width=4)
        draw.text((scaled[0]+4,max(0,scaled[1]-27)),group['specimen_number'],fill='#9E1000',font=font,stroke_width=1,stroke_fill='white')
    overview.save(out/'grouping-preview.jpg',quality=94)
    print(json.dumps({'manifest':str(out/'groups-manifest.json'),'groups':len(groups)},ensure_ascii=False))
    return manifest


def verify(manifest_path):
    manifest=json.loads(manifest_path.read_text())
    source=Path(manifest['source']['path'])
    if digest(source)!=manifest['source']['sha256']:
        raise ValueError('Original photograph no longer matches the source fingerprint')
    image,_=open_source(source)
    checked=0
    shared=manifest.get('shared_scale')
    if shared is not None:
        expected=image.crop(shared['box'])
        with Image.open(shared['path']) as actual:
            if actual.mode!=expected.mode or actual.size!=expected.size or actual.tobytes()!=expected.tobytes():
                raise ValueError('Shared scale pixels differ from original photograph')
        checked+=1
    for group in manifest['groups']:
        entries=[{'box':group['group_box'],'path':group['path']}]+[v for v in group['components'].values() if v is not None]
        for entry in entries:
            expected=image.crop(entry['box'])
            with Image.open(entry['path']) as actual:
                if actual.mode!=expected.mode or actual.size!=expected.size or actual.tobytes()!=expected.tobytes():
                    raise ValueError(f"Pixels differ from the source crop: {entry['path']}")
            checked+=1
    if len(manifest['groups'])!=manifest.get('expected_groups',len(manifest['groups'])):
        raise ValueError('Manifest group coverage differs from expected_groups')
    result={'source_unchanged':True,'pixel_identical_crops':checked,'groups':len(manifest['groups']),
            'shared_scale_crops':int(shared is not None),
            'pairing_review_is_separate':True,'human_review_pending':manifest['human_review_pending']}
    (manifest_path.parent/'crop-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    subs=parser.add_subparsers(dest='command',required=True)
    prep=subs.add_parser('prepare'); prep.add_argument('source',type=Path); prep.add_argument('--work',type=Path,required=True)
    export=subs.add_parser('split'); export.add_argument('source',type=Path); export.add_argument('plan',type=Path); export.add_argument('--out',type=Path,required=True); export.add_argument('--padding',type=int,default=12)
    check=subs.add_parser('verify'); check.add_argument('manifest',type=Path)
    args=parser.parse_args()
    if args.command=='prepare': prepare(args.source,args.work)
    elif args.command=='split':
        if not 0<=args.padding<=100:
            raise ValueError('padding must be between 0 and 100 normalized units')
        split(args.source,args.plan,args.out,args.padding)
    else: print(json.dumps(verify(args.manifest),ensure_ascii=False))


if __name__=='__main__':
    main()
