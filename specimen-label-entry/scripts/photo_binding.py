"""Bind reviewed complete photos to existing records without rewriting specimen data."""
from copy import deepcopy
import json
from pathlib import Path

from complete_photos import check_final_review
from photo_groups import digest
from photo_delivery import export as export_photos


def bind(records, manifests):
    batch = json.loads(records.read_text()); result = deepcopy(batch); selected = {}
    for path in manifests:
        proof = json.loads(path.read_text())
        if proof.get('kind') != 'complete_specimen_photographs':
            raise ValueError('Bind visually reviewed complete photograph manifests')
        by_id = {g['specimen_number']: g for g in proof['plan']['groups']}
        for entry in proof['images']:
            sid = entry['specimen_number']
            if sid in selected:
                raise ValueError(f'{sid}: choose one reviewed source; repeated photographs are ambiguous')
            check_final_review(entry.get('final_review'), entry)
            if digest(entry['path']) != entry['sha256']:
                raise ValueError(f'{sid}: candidate changed after review')
            selected[sid] = (path, proof, entry, by_id[sid])
    ids = [r['values']['标本号'] for r in batch['records']]
    if not ids or len(set(ids)) != len(ids) or set(ids) != set(selected):
        raise ValueError('Reviewed images must cover every selected catalogue record exactly once')
    for old, record in zip(batch['records'], result['records']):
        sid = old['values']['标本号']; path, proof, entry, group = selected[sid]
        source_info = batch['sources'][old['source']]
        source = Path(source_info['path']).resolve()
        if source != Path(proof['source']).resolve() or digest(source) != proof['source_sha256']:
            raise ValueError(f'{sid}: reviewed photograph differs from the record source; resolve the source association')
        record['image_provenance'] = {'original': str(source), 'source_sha256': proof['source_sha256'],
            'complete_photo': {'path': entry['path'], 'manifest': str(path.resolve()),
                'raw_path': entry['raw_path'], 'coordinate_space': 'oriented_native_pixels',
                'reading_rotation': proof['plan']['reading_rotation'], 'group_box': group['group_box'],
                'reviewer': entry['final_review']['reviewer']}}
        if record['values'] != old['values']:
            raise ValueError('Image binding must preserve all specimen values')
    return result


def export_linked(records, out, records_out, manifests):
    linked = records_out.with_name(records_out.stem+'.image-linked.json')
    binding_proof = records_out.with_name(records_out.stem+'.image-binding.json')
    if any(p.exists() for p in (out, records_out, linked, binding_proof)):
        raise ValueError('Use fresh delivery paths; existing records and images are retained')
    # Bind to the actual catalogue and source, not to an old crop path. Source
    # records remain intact as the history of the previous extraction.
    result = bind(records, manifests)
    records_out.parent.mkdir(parents=True, exist_ok=True)
    linked.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    original_sha = digest(records)
    proof = export_photos(linked, out, records_out, manifests, require_complete=True)
    if digest(records) != original_sha:
        raise ValueError('Source records changed during delivery; keep the candidates for review')
    binding_proof.write_text(json.dumps({'kind': 'photo_record_binding',
        'source_records': str(records.resolve()), 'source_records_sha256': original_sha,
        'linked_records': str(linked.resolve()), 'linked_records_sha256': digest(linked),
        'records': str(records_out.resolve()), 'records_sha256': digest(records_out),
        'specimen_values_unchanged_during_binding': True, 'images': len(proof['images']),
        'manifests': [str(p.resolve()) for p in manifests],
        'human_review_pending': True}, ensure_ascii=False, indent=2))
    return proof
