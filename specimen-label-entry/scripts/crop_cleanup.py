#!/usr/bin/env python3
"""Audit crop boundaries and remove reviewed foreign fragments in display copies."""
import argparse
import json
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from photo_groups import digest, verify as verify_crops
from precision_masks import clean_background


def resolve_target(manifest, sid, target):
    group = next((g for g in manifest['groups'] if g['specimen_number'] == sid), None)
    if group is None:
        raise ValueError('Cleanup entry has an unknown catalogue ID')
    if target == 'group':
        return Path(group['path'])
    if target not in ('specimen', 'collection', 'catalogue', 'identification') or not group['components'].get(target):
        raise ValueError('Cleanup target is not an existing group/component crop')
    return Path(group['components'][target]['path'])


def boundary_audit(image):
    gray = np.asarray(image.convert('L'))
    threshold = max(25, min(float(np.percentile(gray, 75))-65, 110))
    dark = gray < threshold
    width = min(4, image.width, image.height)
    strips = {'top': dark[:width], 'bottom': dark[-width:], 'left': dark[:, :width], 'right': dark[:, -width:]}
    fractions = {name: round(float(np.mean(strip)), 5) for name, strip in strips.items()}
    return {'dark_edge_fraction': fractions, 'inspect_edges': [name for name, value in fractions.items() if value > .015],
            'meaning': 'Review possible label remnants or clipped appendages; darkness alone does not authorize erasure'}


def coordinate_guide(image, path):
    guide=image.convert('RGB')
    draw=ImageDraw.Draw(guide)
    step=max(25,round(max(image.size)/15/25)*25)
    font=ImageFont.load_default(size=max(14,step//8))
    for x in range(0,image.width,step):
        draw.line((x,0,x,image.height),fill='#d15a54')
        draw.text((x+2,2),str(x),fill='#b00000',font=font,stroke_width=1,stroke_fill='white')
    for y in range(0,image.height,step):
        draw.line((0,y,image.width,y),fill='#d15a54')
        draw.text((2,y+2),str(y),fill='#b00000',font=font,stroke_width=1,stroke_fill='white')
    guide.save(path)


def prepare(manifest_path, out):
    verify_crops(manifest_path)
    if out.exists():
        raise ValueError('Choose a fresh crop-review directory')
    manifest = json.loads(manifest_path.read_text())
    out.mkdir(parents=True)
    entries = []
    for group in manifest['groups']:
        for target in ['group', 'specimen']:
            source = resolve_target(manifest, group['specimen_number'], target)
            with Image.open(source) as file:
                image = file.copy()
            path = out / f'{group["specimen_number"]}__{target}__coordinates.png'
            coordinate_guide(image,path)
            entries.append({'specimen_number': group['specimen_number'], 'target': target,
                            'source': str(source), 'source_sha256': digest(source), 'image_size': list(image.size),
                            'coordinate_space': 'crop_pixels', 'coordinate_guide': str(path.resolve()),
                            'audit': boundary_audit(image)})
    result = {'source_manifest': str(manifest_path.resolve()), 'source_manifest_sha256': digest(manifest_path), 'entries': entries}
    if manifest.get('shared_scale'):
        source=Path(manifest['shared_scale']['path'])
        with Image.open(source) as file:image=file.copy()
        path=out/'shared-scale__coordinates.png';coordinate_guide(image,path)
        result['shared_scale_review']={'source':str(source.resolve()),'source_sha256':digest(source),
            'image_size':list(image.size),'coordinate_space':'crop_pixels','coordinate_guide':str(path.resolve())}
    (out/'crop-review.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def apply(manifest_path, plan_path, out):
    verify_crops(manifest_path)
    manifest = json.loads(manifest_path.read_text())
    plan = json.loads(plan_path.read_text())
    if plan.get('source_manifest_sha256') != digest(manifest_path):
        raise ValueError('Cleanup plan belongs to a different crop manifest')
    entries = plan.get('entries', [])
    keys = [(e.get('specimen_number'), e.get('target')) for e in entries]
    if not entries or len(set(keys)) != len(keys):
        raise ValueError('Choose distinct cleanup targets')
    if out.exists():
        raise ValueError('Choose a fresh cleaned-copy directory')
    # Validate and render every entry before creating output files.
    rendered = []
    for entry in entries:
        source = resolve_target(manifest, entry['specimen_number'], entry['target'])
        with Image.open(source) as file:
            image = file.copy()
        cleaned, checks = clean_background(image, entry, digest(source))
        rendered.append((entry, source, cleaned, checks))
    out.mkdir(parents=True)
    result = {'schema_version': 1, 'kind': 'reviewed_crop_cleanup', 'source_manifest': str(manifest_path.resolve()),
              'source_manifest_sha256': digest(manifest_path), 'human_review_pending': True, 'images': []}
    for entry, source, image, checks in rendered:
        destination = out/f'{entry["specimen_number"]}__{entry["target"]}__clean.png'
        image.save(destination)
        result['images'].append({'specimen_number': entry['specimen_number'], 'target': entry['target'],
                                 'source': str(source.resolve()), 'path': str(destination.resolve()),
                                 'plan': entry, 'checks': checks})
    path = out/'cleanup-manifest.json'
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    verify(path)
    return result


def verify(path):
    result = json.loads(path.read_text())
    if result.get('kind') != 'reviewed_crop_cleanup' or result.get('schema_version') != 1 or not result.get('images'):
        raise ValueError('Expected a nonempty reviewed crop-cleanup manifest')
    source_manifest = Path(result['source_manifest'])
    verify_crops(source_manifest)
    if digest(source_manifest) != result['source_manifest_sha256']:
        raise ValueError('Cleanup source manifest changed')
    manifest = json.loads(source_manifest.read_text())
    seen = set()
    for entry in result['images']:
        key = entry['specimen_number'], entry['target']
        if key != (entry['plan'].get('specimen_number'), entry['plan'].get('target')):
            raise ValueError('Cleanup plan target differs from its output')
        if key in seen:
            raise ValueError('Duplicate cleaned crop')
        seen.add(key)
        source = resolve_target(manifest, *key)
        if source.resolve() != Path(entry['source']).resolve():
            raise ValueError('Cleaned crop has the wrong source')
        with Image.open(source) as file:
            original = file.copy()
        expected, checks = clean_background(original, entry['plan'], digest(source))
        with Image.open(entry['path']) as actual:
            if actual.mode != expected.mode or actual.size != expected.size or actual.tobytes() != expected.tobytes():
                raise ValueError('Cleaned display copy differs from reviewed masks')
        if entry['checks'] != checks:
            raise ValueError('Cleanup protection evidence differs')
    checks = {'cleaned_images': len(seen), 'raw_crops_unchanged': True, 'protected_pixels_unchanged': True,
              'outside_cleanup_mask_unchanged': True, 'geometry_scale_factor': 1, 'human_review_pending': True}
    (path.parent/'cleanup-verification.json').write_text(json.dumps(checks, ensure_ascii=False, indent=2))
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    preview = sub.add_parser('prepare'); preview.add_argument('manifest', type=Path); preview.add_argument('--out', type=Path, required=True)
    clean = sub.add_parser('apply'); clean.add_argument('manifest', type=Path); clean.add_argument('plan', type=Path); clean.add_argument('--out', type=Path, required=True)
    check = sub.add_parser('verify'); check.add_argument('manifest', type=Path)
    args = parser.parse_args()
    result = prepare(args.manifest, args.out) if args.command == 'prepare' else (
        apply(args.manifest, args.plan, args.out) if args.command == 'apply' else verify(args.manifest))
    print(json.dumps({'command': args.command, 'images': result.get('cleaned_images', len(result.get('images', result.get('entries', [])))),
                      'human_review_pending': True}, ensure_ascii=False))


if __name__ == '__main__':
    main()
