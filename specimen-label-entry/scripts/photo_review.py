#!/usr/bin/env python3
"""Inventory photos and retry recognition from native source pixels, without annotations."""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import re
from PIL import Image
from photo_groups import digest,open_source


def number_candidates(text,digits=7,prefix=''):
    if not isinstance(digits,int) or isinstance(digits,bool) or not 1<=digits<=64 or prefix and not prefix.isdigit():
        raise ValueError('Use a positive digit count and an optional numeric prefix from this batch')
    pattern=rf'(?<![\d.])\d{{{digits}}}(?!\d|\.\d)'
    return list(dict.fromkeys(value for value in re.findall(pattern,text) if value.startswith(prefix)))


def inventory(photos,out,ocr=None,digits=7,prefix=''):
    if out.exists():raise ValueError('Choose a new inventory output')
    photos=[p.resolve() for p in photos]
    if not photos or len(set(photos))!=len(photos):raise ValueError('Choose distinct source photographs')
    stems=Counter(p.stem.lower() for p in photos)
    pages=json.loads(ocr.read_text()) if ocr else []
    if not isinstance(pages,list):raise ValueError('OCR must be a list of image/lines objects')
    entries=[]
    for photo in photos:
        with Image.open(photo) as image:
            exif=image.getexif();nested=exif.get_ifd(34665)
            timestamp=nested.get(36867) or exif.get(36867) or exif.get(306)
        image,orientation=open_source(photo)
        rows=[]
        for page in pages:
            if Path(page['image']).resolve()!=photo and (stems[photo.stem.lower()]!=1 or Path(page['image']).stem.lower()!=photo.stem.lower()):continue
            for line in page.get('lines',[]):
                text=line.get('text','');candidates=number_candidates(text,digits,prefix)
                if candidates:rows.append({'raw_text':text,'numbers':candidates,
                    'kind':'range_or_listing' if re.search(r'\d\s*[-–—~至到]\s*\d',text) else 'candidate',
                    'visually_verified':False})
        entries.append({'path':str(photo),'sha256':digest(photo),'capture_time':timestamp,'display_size':list(image.size),
            'exif_orientation':orientation,'ocr_candidates':rows,'classification':'unreviewed',
            'ocr_name_ambiguous':stems[photo.stem.lower()]>1})
    entries.sort(key=lambda e:(e['capture_time'] is None,e['capture_time'] or '',e['path']))
    for index,entry in enumerate(entries,1):entry['source_key']=f'photo{index:04d}'
    result={'purpose':'photo_inventory; time and number ranges suggest grouping, never create specimen IDs',
        'digit_count':digits,'number_prefix':prefix,'photos':entries,'human_review_pending':True}
    out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    return result


def checked_source(manifest):
    source=Path(manifest['source']['path'])
    if digest(source)!=manifest['source']['sha256']:raise ValueError('Source photograph SHA-256 differs')
    image,_=open_source(source)
    if list(image.size)!=manifest['source']['display_size']:raise ValueError('Source dimensions differ')
    return source,image


def reading_preview(native,factor,rotation):
    if isinstance(factor,bool) or not isinstance(factor,int) or not 1<=factor<=4 or type(rotation) is not int or rotation not in [0,90,180,270]:
        raise ValueError('Use preview factor 1..4 and rotation 0, 90, 180 or 270 degrees counterclockwise')
    preview=native.resize((native.width*factor,native.height*factor),Image.Resampling.LANCZOS) if factor!=1 else native.copy()
    if rotation:preview=preview.transpose({90:Image.Transpose.ROTATE_90,180:Image.Transpose.ROTATE_180,270:Image.Transpose.ROTATE_270}[rotation])
    return preview


def save_views(source,image,regions,out,purpose,factor=1,extra=None,rotation=0):
    if type(rotation) is not int or rotation not in [0,90,180,270]:raise ValueError('Invalid reading rotation')
    if out.exists():raise ValueError('Choose a fresh source-review directory')
    before=digest(source);out.mkdir(parents=True)
    result={'schema_version':1,'purpose':purpose,'source':{'path':str(source.resolve()),'sha256':before,
        'display_size':list(image.size)},'annotations':False,'review_only':True,'human_review_pending':True,
        **(extra or {}),'views':[]}
    for name,box,metadata in regions:
        native=image.crop(box);path=out/(name+'__native.png');native.save(path)
        entry={'name':name,'source_box':box,'native_path':str(path.resolve()),'native_size':list(native.size),**metadata}
        if factor!=1 or rotation:
            preview=reading_preview(native,factor,rotation)
            path=out/(name+f'__{factor}x'+(f'-r{rotation}' if rotation else '')+'.png');preview.save(path)
            entry.update({'preview_path':str(path.resolve()),'preview_factor':factor,'preview_rotation':rotation,'preview_size':list(preview.size),
                          'preview_use':'OCR/viewing only; resampled preview is not scale-calibrated evidence'})
        result['views'].append(entry)
    if digest(source)!=before:raise ValueError('Source changed during review preparation')
    (out/'review-manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    verify(out/'review-manifest.json')
    return result


def cards(manifest_path,out,specimens,padding=60,factor=2,region='collection',rotation=0):
    if region not in ['collection','group']:raise ValueError('Select a collection card or the explicit whole group')
    if isinstance(padding,bool) or not isinstance(padding,(int,float)) or not math.isfinite(padding) or not 0<=padding<=1000:
        raise ValueError('Padding uses 0..1000 normalized whole-photo units')
    if isinstance(factor,bool) or not isinstance(factor,int) or not 1<=factor<=4:raise ValueError('Use preview factor 1..4')
    manifest=json.loads(manifest_path.read_text());source,image=checked_source(manifest)
    by_id={g['specimen_number']:g for g in manifest['groups']}
    if len(by_id)!=len(manifest['groups']) or any(not re.fullmatch(r'[A-Za-z0-9()._-]+',sid) for sid in by_id):
        raise ValueError('Crop manifest must use distinct safe catalogue IDs')
    if not specimens or len(set(specimens))!=len(specimens) or set(specimens)-set(by_id):raise ValueError('Choose distinct existing catalogue IDs')
    regions=[]
    for sid in specimens:
        component=by_id[sid]['components'].get('collection')
        if region=='collection' and component is None:raise ValueError('Missing collection box: use --region group or inspect source views instead of guessing a card location')
        initial=by_id[sid]['group_box'] if region=='group' else component['box']
        x0,y0,x1,y1=initial;px=image.width*padding/1000;py=image.height*padding/1000
        box=[max(0,math.floor(x0-px)),max(0,math.floor(y0-py)),min(image.width,math.ceil(x1+px)),min(image.height,math.ceil(y1+py))]
        if not 0<=x0<x1<=image.width or not 0<=y0<y1<=image.height:raise ValueError('Collection box is outside source bounds')
        regions.append((sid,box,{'specimen_number':sid,'initial_'+region+'_box':initial,'region':region}))
    return save_views(source,image,regions,out,'expanded_card_review' if region=='collection' else 'expanded_group_review',factor,
        {'source_manifest':str(manifest_path.resolve()),'source_manifest_sha256':digest(manifest_path),
         'padding':padding,'padding_coordinate_space':'normalized_0_1000'},rotation=rotation)


def source_views(source,out,layout='halves',segments=None,rotation=0):
    if layout not in ['whole','halves','bands']:raise ValueError('Choose whole, halves or bands')
    image,_=open_source(source)
    count=1 if layout=='whole' else segments or (2 if layout=='halves' else 3)
    length=image.width if layout=='halves' else image.height
    if isinstance(count,bool) or not isinstance(count,int) or not 1<=count<=length:raise ValueError('Choose a positive partition count within source dimensions')
    regions=[]
    for index in range(count):
        a=index*length//count;b=(index+1)*length//count
        box=[a,0,b,image.height] if layout=='halves' else [0,a,image.width,b]
        regions.append((f'source-{index+1:02d}',box,{}))
    return save_views(source,image,regions,out,'direct_source_review',extra={'layout':layout,'segments':count},rotation=rotation)


def verify(path):
    result=json.loads(path.read_text());_,image=checked_source(result)
    if result.get('annotations') is not False or not result.get('views'):raise ValueError('Expected unannotated source views')
    for entry in result['views']:
        x0,y0,x1,y1=entry['source_box']
        if not (0<=x0<x1<=image.width and 0<=y0<y1<=image.height):raise ValueError('Review box outside native source')
        expected=image.crop(entry['source_box'])
        with Image.open(entry['native_path']) as actual:
            if actual.mode!=expected.mode or actual.size!=expected.size or actual.tobytes()!=expected.tobytes():raise ValueError('Native review pixels changed')
        if entry.get('preview_path'):
            expected=reading_preview(expected,entry['preview_factor'],entry.get('preview_rotation',0))
            with Image.open(entry['preview_path']) as actual:
                if actual.mode!=expected.mode or actual.size!=expected.size or actual.tobytes()!=expected.tobytes():raise ValueError('Review enlargement differs')
    return {'source_unchanged':True,'native_views_verified':len(result['views']),'review_only':True}


def main():
    p=argparse.ArgumentParser(description=__doc__);subs=p.add_subparsers(dest='command',required=True)
    inv=subs.add_parser('inventory');inv.add_argument('photos',type=Path,nargs='+');inv.add_argument('--ocr',type=Path)
    inv.add_argument('--digits',type=int,default=7);inv.add_argument('--number-prefix',default='');inv.add_argument('--out',type=Path,required=True)
    card=subs.add_parser('cards');card.add_argument('manifest',type=Path);card.add_argument('--specimens',nargs='+',required=True)
    card.add_argument('--padding',type=float,default=60);card.add_argument('--factor',type=int,default=2);card.add_argument('--out',type=Path,required=True)
    card.add_argument('--region',choices=['collection','group'],default='collection')
    card.add_argument('--rotation',type=int,choices=[0,90,180,270],default=0)
    views=subs.add_parser('source');views.add_argument('photo',type=Path);views.add_argument('--layout',choices=['whole','halves','bands'],default='halves')
    views.add_argument('--segments',type=int);views.add_argument('--out',type=Path,required=True)
    views.add_argument('--rotation',type=int,choices=[0,90,180,270],default=0)
    check=subs.add_parser('verify');check.add_argument('manifest',type=Path)
    args=p.parse_args()
    if args.command=='inventory':r=inventory(args.photos,args.out,args.ocr,args.digits,args.number_prefix)
    elif args.command=='cards':r=cards(args.manifest,args.out,args.specimens,args.padding,args.factor,args.region,args.rotation)
    elif args.command=='source':r=source_views(args.photo,args.out,args.layout,args.segments,args.rotation)
    else:r=verify(args.manifest)
    print(json.dumps({'command':args.command,'photos':len(r.get('photos',[])),'views':len(r.get('views',[])),
                      'native_views_verified':r.get('native_views_verified'),'human_review_pending':True},ensure_ascii=False))


if __name__=='__main__':main()
