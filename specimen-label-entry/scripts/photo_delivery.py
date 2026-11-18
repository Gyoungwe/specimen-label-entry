#!/usr/bin/env python3
"""Copy verified group images to catalogue-based delivery names without changing IDs."""
import argparse
from copy import deepcopy
import json
from pathlib import Path,PurePosixPath
import re
import shutil
from PIL import Image
from photo_groups import digest,open_source
from shared_scale import verify as verify_compositions
from complete_photos import verify as verify_complete_photos


def filename(sid,strip_prefix='IOZ(E)'):
    if not isinstance(sid,str) or not re.fullmatch(r'[A-Za-z0-9()._-]+',sid):raise ValueError('Use the visually verified catalogue ID, not a path')
    stem=sid[len(strip_prefix):] if strip_prefix and sid.startswith(strip_prefix) else sid
    if not stem:raise ValueError('Catalogue ID has no filename stem')
    return stem+'-1.png'


def composition_index(paths):
    result={}
    for path in paths:
        manifest=json.loads(path.read_text())
        if manifest.get('kind')=='complete_specimen_photographs':
            verify_complete_photos(path)
            for entry in manifest['images']:
                key=Path(entry['path']).resolve()
                if key in result:raise ValueError('Repeated image path across manifests')
                result[key]=(manifest,entry)
            continue
        verify_compositions(path)
        for entry in manifest['images']:
            if entry['target']=='group':
                result[Path(entry['path']).resolve()]=(manifest,entry)
    return result


def checked_image(record,sources,composed,require_complete=False):
    provenance=record.get('image_provenance') or {}
    source=Path(provenance.get('original') or sources[record['source']]['path'])
    expected_sha=provenance.get('source_sha256') or sources[record['source']].get('sha256')
    if not expected_sha or digest(source)!=expected_sha:raise ValueError('Delivery source photograph SHA-256 differs')
    complete=provenance.get('complete_photo')
    if complete:
        path=Path(complete['path']).resolve()
        if path not in composed:raise ValueError('Provide the visually reviewed five-part photograph manifest')
        manifest,entry=composed[path]
        if (manifest.get('kind')!='complete_specimen_photographs' or
            manifest['source_sha256']!=expected_sha or Path(manifest['source']).resolve()!=source.resolve() or
            entry['specimen_number']!=record['values']['标本号']):
            raise ValueError('Complete photograph has the wrong catalogue/source pairing')
        return path
    if require_complete:
        raise ValueError('Delivery requires the complete number paper, specimen, collection paper and both ruler views')
    image,_=open_source(source);box=provenance.get('group_box')
    if (not isinstance(box,list) or len(box)!=4 or any(isinstance(v,bool) or not isinstance(v,int) for v in box) or
        not (0<=box[0]<box[2]<=image.width and 0<=box[1]<box[3]<=image.height)):
        raise ValueError('Delivery provenance needs an in-bounds native group_box; x uses width and y uses height')
    raw=Path(provenance['group_image']);expected=image.crop(box)
    with Image.open(raw) as actual:
        if actual.format!='PNG' or actual.mode!=expected.mode or actual.size!=expected.size or actual.tobytes()!=expected.tobytes():
            raise ValueError('Group evidence is not the declared original-pixel source crop')
    scale=provenance.get('scale_composition')
    if not scale:return raw
    path=Path(scale['path']).resolve()
    if path not in composed:raise ValueError('Provide the verified composition manifest for a composed delivery image')
    manifest,entry=composed[path]
    if (manifest['source_sha256']!=expected_sha or entry['specimen_number']!=record['values']['标本号'] or
        Path(entry['base_path']).resolve()!=raw.resolve()):raise ValueError('Delivery composition has the wrong catalogue/source pairing')
    return path


def export(records,out,records_out,compositions=(),strip_prefix='IOZ(E)',require_complete=False):
    if (out.exists() or records_out.exists() or records_out.resolve()==records.resolve() or
        records_out.suffix.lower()!='.json' or records_out.resolve()==(out/'delivery-manifest.json').resolve()):
        raise ValueError('Choose a fresh image directory and records output; retain all inputs')
    batch=json.loads(records.read_text());items=batch.get('records',[])
    ids=[r.get('values',{}).get('标本号') for r in items]
    names=[filename(sid,strip_prefix) for sid in ids]
    if not ids or len(set(ids))!=len(ids) or len(set(names))!=len(names):
        raise ValueError('Resolve repeated photographs/catalogue IDs and filename collisions before delivery')
    composed=composition_index(compositions)
    selected=[checked_image(record,batch['sources'],composed,require_complete) for record in items]
    result=deepcopy(batch);proof={'schema_version':1,'kind':'verified_photo_delivery','source_records':str(records.resolve()),
        'source_records_sha256':digest(records),'records':str(records_out.resolve()),
        'composition_manifests':[str(p.resolve()) for p in compositions],
        'strip_prefix':strip_prefix,'image_prefix':out.name,'images':[],'human_review_pending':True}
    if require_complete:proof['require_complete_photos']=True
    out.mkdir(parents=True)
    for record,sid,name,source in zip(result['records'],ids,names,selected):
        destination=out/name;shutil.copyfile(source,destination)
        if digest(destination)!=digest(source):raise ValueError('Delivered image differs from the selected source')
        relative=str(PurePosixPath(out.name)/name)
        record['values']['图像']=relative
        record.setdefault('image_provenance',{})['delivery_image']={'path':str(destination.resolve()),
            'source_path':str(source.resolve()),'sha256':digest(source),'method':'byte_copy; catalogue ID unchanged'}
        proof['images'].append({'specimen_number':sid,'path':str(destination.resolve()),'relative_path':relative,
            'source_path':str(source.resolve()),'sha256':digest(source)})
    records_out.parent.mkdir(parents=True,exist_ok=True);records_out.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    proof['records_sha256']=digest(records_out)
    path=out/'delivery-manifest.json';path.write_text(json.dumps(proof,ensure_ascii=False,indent=2));verify(path)
    return proof


def verify(path):
    proof=json.loads(path.read_text())
    if proof.get('kind')!='verified_photo_delivery' or proof.get('schema_version')!=1:raise ValueError('Expected a delivery manifest')
    records=Path(proof['records']);original=Path(proof['source_records'])
    if digest(records)!=proof['records_sha256'] or digest(original)!=proof['source_records_sha256']:raise ValueError('Delivery records changed')
    batch=json.loads(records.read_text());old=json.loads(original.read_text())
    by_id={r['values']['标本号']:r for r in batch['records']};originals={r['values']['标本号']:r for r in old['records']}
    ids=[e['specimen_number'] for e in proof['images']]
    if len(ids)!=len(set(ids)) or len(by_id)!=len(batch['records']) or set(ids)!=set(by_id) or set(ids)!=set(originals):
        raise ValueError('Delivery must cover every unique catalogue ID exactly once')
    composed=composition_index([Path(p) for p in proof.get('composition_manifests',[])])
    expected_batch=deepcopy(old)
    expected_records={r['values']['标本号']:r for r in expected_batch['records']}
    for entry in proof['images']:
        sid=entry['specimen_number'];image=Path(entry['path'])
        expected=checked_image(originals[sid],old['sources'],composed,proof.get('require_complete_photos',False))
        if (expected.resolve()!=Path(entry['source_path']).resolve() or digest(expected)!=entry['sha256'] or
            digest(image)!=entry['sha256'] or image.name!=filename(sid,proof['strip_prefix']) or
            by_id[sid]['values'].get('图像')!=entry['relative_path'] or
            entry['relative_path']!=str(PurePosixPath(proof['image_prefix'])/image.name)):
            raise ValueError('Delivery image, filename or record reference differs')
        expected_records[sid]['values']['图像']=entry['relative_path']
        expected_records[sid].setdefault('image_provenance',{})['delivery_image']={
            'path':str(image.resolve()),'source_path':str(expected.resolve()),'sha256':entry['sha256'],
            'method':'byte_copy; catalogue ID unchanged'}
    if expected_batch!=batch:raise ValueError('Delivery changed record values or provenance beyond the image reference')
    if {p.resolve() for p in path.parent.glob('*.png')}!={Path(e['path']).resolve() for e in proof['images']}:
        raise ValueError('Delivery PNG count differs from the record coverage')
    return {'unique_records':len(ids),'verified_images':len(ids),'catalogue_ids_unchanged':True,
        'image_bytes_unchanged':True,'human_review_pending':True}


def main():
    p=argparse.ArgumentParser(description=__doc__);subs=p.add_subparsers(dest='command',required=True)
    copy=subs.add_parser('export');copy.add_argument('records',type=Path);copy.add_argument('--out',type=Path,required=True)
    copy.add_argument('--records-out',type=Path,required=True);copy.add_argument('--compositions',nargs='*',type=Path,default=[])
    copy.add_argument('--strip-prefix',default='IOZ(E)')
    copy.add_argument('--require-complete',action='store_true',help='Require hash-bound visual reviews of all five complete image parts')
    check=subs.add_parser('verify');check.add_argument('manifest',type=Path)
    a=p.parse_args()
    r=export(a.records,a.out,a.records_out,a.compositions,a.strip_prefix,a.require_complete) if a.command=='export' else verify(a.manifest)
    print(json.dumps({'command':a.command,'images':len(r['images']) if 'images' in r else r['verified_images'],
                      'catalogue_ids_unchanged':True,'human_review_pending':True},ensure_ascii=False))


if __name__=='__main__':main()
