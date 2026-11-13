#!/usr/bin/env python3
"""Copy photographed scale ink at 1:1 geometry into unobstructed image backgrounds."""
import argparse
import hashlib
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from photo_groups import digest, verify as verify_crops
from scale_overlay import extract_ink,compose_overlay,protected_unchanged


def equal_region(image,box,expected):
    actual=image.crop(box)
    return actual.mode==expected.mode and actual.size==expected.size and actual.tobytes()==expected.tobytes()


def append_scale(base,scale,margin=24):
    if base.mode!=scale.mode:
        raise ValueError('Scale and evidence must have the same source colour mode')
    caption_height=28
    width=max(base.width,scale.width+2*margin,180)
    height=base.height+scale.height+2*margin+caption_height
    canvas=Image.new(base.mode,(width,height),'white')
    base_xy=((width-base.width)//2,0)
    scale_xy=(width-margin-scale.width,base.height+margin+caption_height)
    canvas.paste(base,base_xy)
    canvas.paste(scale,scale_xy)
    draw=ImageDraw.Draw(canvas)
    draw.line((0,base.height,width,base.height),fill='gray',width=1)
    draw.text((margin,base.height+8),'Shared scale (1:1)',
              font=ImageFont.load_default(size=14),fill='black')
    base_box=[base_xy[0],0,base_xy[0]+base.width,base.height]
    scale_box=[scale_xy[0],scale_xy[1],scale_xy[0]+scale.width,scale_xy[1]+scale.height]
    if not equal_region(canvas,base_box,base) or not equal_region(canvas,scale_box,scale):
        raise ValueError('Composition changed specimen or scale pixels')
    return canvas,base_box,scale_box


def compose_target(base,scale,group,target,shared):
    outer=group['group_box'];box=shared['box']
    if target=='group' and outer[0]<=box[0] and outer[1]<=box[1] and box[2]<=outer[2] and box[3]<=outer[3]:
        local=[box[0]-outer[0],box[1]-outer[1],box[2]-outer[0],box[3]-outer[1]]
        if not equal_region(base,local,scale):
            raise ValueError('The scale visible in the group is not the same source crop')
        return base.copy(),[0,0,base.width,base.height],local,'original_scale_visible'
    canvas,base_box,scale_box=append_scale(base,scale)
    return canvas,base_box,scale_box,'appended_footer'


def add(manifest_path,out,targets=('group','specimen'),style='overlay'):
    if out.exists():
        raise ValueError('Choose a new composed-image directory')
    if not targets or len(set(targets))!=len(targets) or set(targets)-{'group','specimen'}:
        raise ValueError('Select group and/or specimen once each')
    if style not in ('overlay','footer'):raise ValueError('Choose overlay or footer style')
    verify_crops(manifest_path)
    manifest=json.loads(manifest_path.read_text())
    shared=manifest.get('shared_scale')
    if not shared or shared.get('visually_checked') is not True:
        raise ValueError('Manifest must contain a visually checked shared_scale from this photograph')
    groups=manifest['groups']
    if len({g['specimen_number'] for g in groups})!=len(groups):
        raise ValueError('Catalogue IDs must be unique')
    if any(g.get('pairing_reviewed') is not True for g in groups):
        raise ValueError('Inspect each specimen-label pairing before composing final images')
    out.mkdir(parents=True)
    scale_path=Path(shared['path'])
    before=digest(scale_path)
    with Image.open(scale_path) as file:
        scale=file.copy()
    composition={'schema_version':2,'style':style,'kind':'shared_scale_composition','source_manifest':str(manifest_path.resolve()),
        'source_sha256':manifest['source']['sha256'],'shared_scale':shared,
        'targets':list(targets),'group_count':len(groups),'scale_factor':1,
        'measurement_status':'not_automatically_calibrated; inspect coplanarity and perspective',
        'human_review_pending':True,'images':[]}
    ink=None
    if style=='overlay':
        ink,extraction=extract_ink(scale)
        ink_path=out/'scale-cutout.png'
        ink.save(ink_path,format='PNG')
        composition['scale_cutout']={**extraction,'path':str(ink_path.resolve()),
            'pixel_sha256':hashlib.sha256(ink.tobytes()).hexdigest()}
    for group in groups:
        for target in targets:
            base_path=Path(group['path'] if target=='group' else group['components']['specimen']['path'])
            with Image.open(base_path) as file:
                base=file.copy()
            canvas,base_box,scale_box,placement=(compose_overlay(base,ink,group,target,shared) if style=='overlay'
                else compose_target(base,scale,group,target,shared))
            if not protected_unchanged(base,canvas,group,target,base_box):
                raise ValueError('A specimen or label was covered by the ruler')
            destination=out/(group['specimen_number']+'.png') if target=='group' else out/'specimens'/(group['specimen_number']+'__specimen.png')
            destination.parent.mkdir(parents=True,exist_ok=True)
            canvas.save(destination,format='PNG',compress_level=6)
            composition['images'].append({'specimen_number':group['specimen_number'],'target':target,
                'path':str(destination.resolve()),'base_path':str(base_path.resolve()),
                'base_sha256':digest(base_path),'base_box':base_box,'scale_box':scale_box,
                'placement':placement,
                'size':list(canvas.size),'pixel_sha256':hashlib.sha256(canvas.tobytes()).hexdigest()})
    if digest(scale_path)!=before:
        raise ValueError('Shared scale changed during composition')
    path=out/'compositions-manifest.json'
    path.write_text(json.dumps(composition,ensure_ascii=False,indent=2))
    result=verify(path)
    print(json.dumps(result,ensure_ascii=False))
    return composition


def verify(path):
    composition=json.loads(path.read_text())
    source_path=Path(composition['source_manifest'])
    verify_crops(source_path)
    source=json.loads(source_path.read_text())
    if source['source']['sha256']!=composition['source_sha256'] or source.get('shared_scale')!=composition['shared_scale']:
        raise ValueError('Composition refers to another photograph or scale')
    targets=composition['targets']
    if not targets or len(set(targets))!=len(targets) or set(targets)-{'group','specimen'}:
        raise ValueError('Invalid composition targets')
    expected={(g['specimen_number'],t) for g in source['groups'] for t in targets}
    actual=[(e['specimen_number'],e['target']) for e in composition['images']]
    if len(set(actual))!=len(actual) or set(actual)!=expected:
        raise ValueError('Each catalogue ID must have exactly one output per requested target')
    by_id={g['specimen_number']:g for g in source['groups']}
    with Image.open(source['shared_scale']['path']) as file:
        scale=file.copy()
    style=composition.get('style','footer')
    if style not in ('overlay','footer'):raise ValueError('Unknown composition style')
    ink=None
    if style=='overlay':
        ink,extraction=extract_ink(scale)
        saved=composition['scale_cutout']
        if any(saved.get(key)!=value for key,value in extraction.items()):
            raise ValueError('Scale extraction geometry or method differs')
        with Image.open(saved['path']) as asset:
            if asset.mode!=ink.mode or asset.size!=ink.size or asset.tobytes()!=ink.tobytes():
                raise ValueError('Transparent scale was altered or resized')
    for entry in composition['images']:
        group=by_id[entry['specimen_number']]
        original=group['path'] if entry['target']=='group' else group['components']['specimen']['path']
        if Path(entry['base_path']).resolve()!=Path(original).resolve() or digest(Path(original))!=entry['base_sha256']:
            raise ValueError('Composed image has the wrong specimen source')
        with Image.open(original) as file:
            base=file.copy()
        expected_image,base_box,scale_box,placement=(compose_overlay(base,ink,group,entry['target'],source['shared_scale']) if style=='overlay'
            else compose_target(base,scale,group,entry['target'],source['shared_scale']))
        with Image.open(entry['path']) as actual_image:
            if (entry.get('placement')!=placement or entry['base_box']!=base_box or entry['scale_box']!=scale_box or
                    actual_image.mode!=expected_image.mode or actual_image.size!=expected_image.size or
                    actual_image.tobytes()!=expected_image.tobytes()):
                raise ValueError(f'Composition pixels or scale size differ: {entry["path"]}')
            if not protected_unchanged(base,actual_image,group,entry['target'],base_box):
                raise ValueError('Specimen/label pixels changed')
    result={'source_unchanged':True,'groups':len(source['groups']),'composed_images':len(actual),
        'scale_regions_verified':len(actual),'style':style,'scale_copies_verified':len(actual),
        'specimen_pixels_unchanged':True,'scale_factor':1,
        'placements':{name:sum(e['placement']==name for e in composition['images']) for name in sorted({e['placement'] for e in composition['images']})},
        'human_review_pending':True}
    (path.parent/'composition-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    subs=parser.add_subparsers(dest='command',required=True)
    export=subs.add_parser('add');export.add_argument('manifest',type=Path);export.add_argument('--out',type=Path,required=True)
    export.add_argument('--targets',nargs='+',choices=('group','specimen'),default=['group','specimen'])
    export.add_argument('--style',choices=('overlay','footer'),default='overlay')
    check=subs.add_parser('verify');check.add_argument('manifest',type=Path)
    args=parser.parse_args()
    if args.command=='add':
        if len(set(args.targets))!=len(args.targets):
            raise ValueError('Each target may be requested once')
        add(args.manifest,args.out,tuple(args.targets),args.style)
    else: print(json.dumps(verify(args.manifest),ensure_ascii=False))
