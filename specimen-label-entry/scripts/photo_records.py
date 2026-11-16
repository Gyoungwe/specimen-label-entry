#!/usr/bin/env python3
"""Join independently transcribed catalogue groups to the existing entry pipeline."""
import argparse
import json
from pathlib import Path, PurePosixPath
from photo_groups import verify
from label_corrections import collection_text_acceptable


def join_records(manifest, transcription, image_prefix='原像素分组',composition=None):
    groups=manifest['groups']
    by_id={g['specimen_number']:g for g in groups}
    if len(by_id)!=len(groups):
        raise ValueError('Duplicate catalogue ID in manifest')
    records=transcription.get('records',[])
    ids=[r.get('specimen_number') for r in records]
    if len(set(ids))!=len(ids) or set(ids)!=set(by_id):
        raise ValueError('Transcription must cover each manifest catalogue ID exactly once')
    composed={}
    if composition is not None:
        if composition.get('source_sha256')!=manifest['source']['sha256']:
            raise ValueError('Shared-scale images belong to a different photograph')
        selected=[e for e in composition['images'] if e['target']=='group']
        composed={e['specimen_number']:e for e in selected}
        if len(composed)!=len(selected) or set(composed)!=set(by_id):
            raise ValueError('Shared-scale group images must cover every specimen exactly once')
    result=[]
    for record in records:
        sid=record['specimen_number']
        group=by_id[sid]
        if group.get('pairing_reviewed') is not True or record.get('visually_checked') is not True:
            raise ValueError(f'{sid}: inspect group pairing and all component crops first')
        values=dict(record.get('values',{}))
        if values.get('标本号',sid)!=sid:
            raise ValueError(f'{sid}: transcribed specimen number differs from group ID')
        if record.get('identification_label_present') is not group['identification_label_present']:
            raise ValueError(f'{sid}: determination-label presence differs from crop manifest')
        raw=record.get('collection_label_text')
        if not collection_text_acceptable(record):
            raise ValueError(f'{sid}: transcribe this group collection label')
        values['标本号']=sid
        # Relative image references stay usable when the delivered batch is moved.
        values['图像']=str(PurePosixPath(image_prefix)/(sid+'.png'))
        item={k:v for k,v in record.items() if k not in ('specimen_number','source','page','values')}
        item.update({'source':'photo1','page':1,'group_id':sid,'record_id':record.get('record_id',sid),'values':values,
                     'image_provenance':{'original':manifest['source']['path'],
                       'source_sha256':manifest['source']['sha256'],'group_box':group['group_box'],
                       'group_image':group['path'],'components':group['components']},
                     'issues':list(dict.fromkeys(group.get('issues',[])+record.get('issues',[])))})
        if composition is not None:
            item['image_provenance']['scale_composition']={'path':composed[sid]['path'],
                'source_scale':composition['shared_scale'],'scale_factor':1,
                'style':composition.get('style','footer'),'placement':composed[sid].get('placement'),
                'scale_cutout':composition.get('scale_cutout'),
                **({'cleanup_manifest':composition['cleanup_manifest'],
                    'cleanup_manifest_sha256':composition['cleanup_manifest_sha256'],
                    'cleaned_base_path':composed[sid].get('cleaned_base_path')}
                   if composition.get('cleanup_manifest') else {}),
                'measurement_status':composition['measurement_status']}
        result.append(item)
    return {'sheet':transcription.get('sheet','biaobenku'),
            'sources':{'photo1':{**manifest['source'],'kind':'image','page_count':1}},
            'records':result,'batch_notes':transcription.get('batch_notes',[]),
            'shared_collection_groups':transcription.get('shared_collection_groups',[]),
            'supplemental_sources':transcription.get('supplemental_sources',[])}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest',type=Path)
    parser.add_argument('transcription',type=Path)
    parser.add_argument('--image-prefix')
    parser.add_argument('--compositions',type=Path,help='Verified shared-scale compositions manifest')
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    verify(args.manifest)
    composition=None
    if args.compositions:
        from shared_scale import verify as verify_compositions
        verify_compositions(args.compositions)
        composition=json.loads(args.compositions.read_text())
    prefix=args.image_prefix or ('带比例尺分组' if composition is not None else '原像素分组')
    batch=join_records(json.loads(args.manifest.read_text()),json.loads(args.transcription.read_text()),prefix,composition)
    if args.out.exists():
        raise ValueError('Choose a new records output path')
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(batch,ensure_ascii=False,indent=2,allow_nan=False))
    print(json.dumps({'records':len(batch['records']),'out':str(args.out)},ensure_ascii=False))


if __name__=='__main__':
    main()
