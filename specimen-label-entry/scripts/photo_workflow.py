#!/usr/bin/env python3
"""Agent-facing photo workflow: native-coordinate guides, staged builds and explicit review gates."""
import argparse
import json
from pathlib import Path
import sys

from PIL import Image, ImageDraw, ImageFont
import numpy as np

from complete_photos import (PARTS, TRANSPOSE, oriented_source, validate_group, build,
                             record_reviews, verify)
from photo_groups import open_source, digest
from photo_binding import export_linked


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2))


def prepare(source, out, rotation=0, ids=()):
    if out.exists():
        raise ValueError('Use a fresh work directory; previous evidence is retained')
    if len(set(ids)) != len(ids):
        raise ValueError('Resolve duplicate catalogue IDs before creating a draft')
    source = source.resolve(); image, exif = open_source(source)
    exif_size = list(image.size)
    if rotation:
        image = image.transpose(TRANSPOSE[rotation])
    plan = {'source_sha256': digest(source), 'exif_display_size': exif_size,
            'reading_rotation': rotation, 'oriented_size': list(image.size),
            'coordinate_space': 'oriented_native_pixels', 'expected_groups': len(ids),
            'native_scale': None, 'groups': []}
    for sid in ids:
        plan['groups'].append({'specimen_number': sid, 'group_box': None,
            'parts': {key: None for key in ('catalogue', 'specimen', 'collection')},
            'pairing_reviewed': False, 'source_review': {'reviewer': '', 'note': '',
                **{key: False for key in PARTS[:-1]}}})
    out.mkdir(parents=True)
    overview = image.convert('RGB'); overview.thumbnail((2200, 2200))
    overview.save(out/'overview.jpg', quality=95)
    grid = overview.copy(); d = ImageDraw.Draw(grid); font = ImageFont.load_default(size=17)
    step = max(100, round(max(image.size)/10/100)*100)
    for x in range(0, image.width, step):
        px = round(x*grid.width/image.width)
        d.line((px, 0, px, grid.height), fill='#35b5cf')
        d.text((px+2, 2), str(x), font=font, fill='#9e0000', stroke_width=1, stroke_fill='white')
    for y in range(0, image.height, step):
        py = round(y*grid.height/image.height)
        d.line((0, py, grid.width, py), fill='#35b5cf')
        d.text((2, py+2), str(y), font=font, fill='#9e0000', stroke_width=1, stroke_fill='white')
    grid.save(out/'native-coordinate-grid.jpg', quality=95)
    save_json(out/'plan.json', plan)
    info = {'status': 'needs_source_review', 'source': str(source), 'source_sha256': digest(source),
            'exif_orientation': exif, 'plan': str((out/'plan.json').resolve()),
            'overview': str((out/'overview.jpg').resolve()),
            'coordinate_grid': str((out/'native-coordinate-grid.jpg').resolve()),
            'coordinate_space': 'oriented_native_pixels', 'oriented_size': list(image.size),
            'preview_size': list(overview.size),
            'next_action': 'View overview/grid, read actual IDs, fill complete source regions and ruler evidence; review flags remain false until inspected.'}
    save_json(out/'source.json', info)
    return info


def check(source, plan_path):
    plan = json.loads(plan_path.read_text()); issues = []; cleanup_notes = []
    try:
        image, _ = oriented_source(source, plan)
    except (ValueError, KeyError, TypeError) as e:
        return {'status': 'needs_source_review', 'issues': [{'code': 'SOURCE_COORDINATES', 'detail': str(e)}]}
    groups = plan.get('groups', [])
    ids = [g.get('specimen_number') for g in groups]
    if not groups or plan.get('expected_groups') != len(groups):
        issues.append({'code': 'INVENTORY', 'detail': 'Read the actual IDs and cover every group exactly once'})
    if len(set(ids)) != len(ids):
        issues.append({'code': 'DUPLICATE_IDS', 'detail': 'Select one source per actual catalogue ID'})
    for group in groups:
        sid = group.get('specimen_number')
        for role in ('catalogue', 'specimen', 'collection'):
            if not group.get('parts', {}).get(role):
                issues.append({'specimen_number': sid, 'code': 'MISSING_REGION', 'part': role})
        try:
            validate_group(group, image, plan.get('native_scale'))
        except (ValueError, KeyError, TypeError) as e:
            issues.append({'specimen_number': sid, 'code': 'SOURCE_REVIEW', 'detail': str(e)})
            continue
        for box in group.get('cleanup', {}).get('exclude_boxes', []):
            pixels = np.asarray(image.crop(box).convert('L'))
            threshold = max(25, min(90, float(np.percentile(pixels, 70))-45))
            if np.count_nonzero(pixels < threshold) < 12:
                cleanup_notes.append({'specimen_number': sid, 'box': box, 'code': 'LITTLE_VISIBLE_INK',
                    'detail': 'Inspect this proposed cleanup on the source; retain original background if no foreign fragment is actually present'})
    return {'status': 'ready_to_build' if not issues else 'needs_source_review',
            'groups': len(groups), 'issues': issues, 'cleanup_notes': cleanup_notes, 'delivery_ready': False,
            'next_action': 'Build candidates, then inspect all generated images' if not issues else 'Return to source; correct the named regions/evidence without shrinking complete subjects'}


def review_assets(manifest_path):
    proof = json.loads(manifest_path.read_text()); root = manifest_path.parent
    out = root/'review'
    if (out/'reviews-template.json').exists():
        raise ValueError('Review template already exists; retain previous inspection records')
    out.mkdir(exist_ok=True)
    entries = proof['images']; contacts = []
    columns = 2 if len(entries) <= 4 else 4
    cell_w, cell_h = (760, 540) if columns == 2 else (510, 440)
    page_size = 16
    for start in range(0, len(entries), page_size):
        chunk = entries[start:start+page_size]
        c = Image.new('RGB', (columns*cell_w, ((len(chunk)+columns-1)//columns)*cell_h), '#ededed')
        d = ImageDraw.Draw(c); font = ImageFont.load_default(size=18)
        for i, entry in enumerate(chunk):
            with Image.open(entry['path']) as actual:
                thumbnail = actual.convert('RGB'); thumbnail.thumbnail((cell_w-16, cell_h-42))
            x, y = i % columns*cell_w, i//columns*cell_h
            d.text((x+8, y+3), entry['specimen_number'], fill='black', font=font)
            c.paste(thumbnail, (x+8, y+32))
        path = out/f'contact-{start//page_size+1:03d}.jpg'; c.save(path, quality=95)
        contacts.append(str(path.resolve()))
    rulers = []
    for entry in entries:
        asset = Image.open(entry['ink_path']).convert('RGBA')
        backgrounds = []
        for colour, suffix in (('white', 'light'), ('#202833', 'dark')):
            preview = Image.new('RGBA', asset.size, colour); preview.alpha_composite(asset)
            path = out/(entry['specimen_number']+'__ruler-'+suffix+'.png')
            preview.convert('RGB').save(path); backgrounds.append(str(path.resolve()))
        rulers.append({'specimen_number': entry['specimen_number'], 'previews': backgrounds})
    template = [{'specimen_number': e['specimen_number'], 'image_sha256': e['sha256'],
                 'reviewer': '', 'note': '', **{role: False for role in PARTS}} for e in entries]
    template_path = out/'reviews-template.json'
    if template_path.exists():
        raise ValueError('Review template already exists; retain previous inspection records')
    save_json(template_path, template)
    result = {'status': 'needs_final_visual_review', 'groups': len(entries),
              'delivery_ready': False,
              'manifest': str(manifest_path.resolve()), 'contacts': contacts, 'rulers': rulers,
              'review_template': str(template_path.resolve()),
              'next_action': 'View each candidate against its source, inspect ruler light/dark previews, then fill the five flags and reviewer/note; use review to record the checks'}
    save_json(out/'review-index.json', result)
    return result


def build_one(source, plan, out):
    readiness = check(source, plan)
    if readiness['status'] != 'ready_to_build':
        return readiness
    build(source, plan, out)
    return review_assets(out/'complete-manifest.json')


def batch(jobs_path, report_path):
    jobs = json.loads(jobs_path.read_text())
    if not isinstance(jobs, list) or not jobs:
        raise ValueError('Supply a nonempty JSON list of source/plan/out jobs')
    # Relative paths are resolved against the job file, never the agent shell cwd.
    def resolve(value):
        path = Path(value)
        return (path if path.is_absolute() else jobs_path.parent/path).resolve()
    resolved = [{key: resolve(job[key]) for key in ('source', 'plan', 'out')} for job in jobs]
    if report_path.resolve() in {jobs_path.resolve(), *(j[key] for j in resolved for key in ('source', 'plan'))}:
        raise ValueError('Write the batch report separately from all inputs')
    if any(report_path.resolve().is_relative_to(j['out']) for j in resolved):
        raise ValueError('Write the batch report outside all job output directories')
    if len({j['out'] for j in resolved}) != len(resolved):
        raise ValueError('Each source job needs a unique fresh output directory')
    results = []
    for job in resolved:
        try:
            result = build_one(job['source'], job['plan'], job['out'])
        except (ValueError, KeyError, OSError, TypeError) as e:
            result = {'status': 'failed', 'error': str(e), 'next_action': 'Inspect the named source/plan; retry in a fresh output directory'}
        results.append({'source': str(job['source']), 'plan': str(job['plan']), 'out': str(job['out']), **result})
        save_json(report_path, {'status': 'in_progress', 'jobs': results})
    failed = sum(r['status'] != 'needs_final_visual_review' for r in results)
    result = {'status': 'needs_source_review' if failed else 'needs_final_visual_review',
              'failed_jobs': failed, 'jobs': results, 'delivery_ready': False}
    save_json(report_path, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__); sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare'); p.add_argument('source', type=Path); p.add_argument('--out', required=True, type=Path)
    p.add_argument('--rotation', type=int, choices=(0, 90, 180, 270), default=0)
    p.add_argument('--ids', nargs='*', default=[])
    for name in ('check', 'build'):
        p = sub.add_parser(name); p.add_argument('source', type=Path); p.add_argument('plan', type=Path)
        if name == 'build': p.add_argument('--out', required=True, type=Path)
    p = sub.add_parser('batch'); p.add_argument('jobs', type=Path); p.add_argument('--report', required=True, type=Path)
    p = sub.add_parser('review'); p.add_argument('manifest', type=Path); p.add_argument('reviews', type=Path)
    p = sub.add_parser('verify'); p.add_argument('manifest', type=Path)
    p = sub.add_parser('export'); p.add_argument('records', type=Path); p.add_argument('--out', required=True, type=Path)
    p.add_argument('--records-out', required=True, type=Path); p.add_argument('--manifests', nargs='+', required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'prepare': result = prepare(args.source, args.out, args.rotation, args.ids)
        elif args.command == 'check': result = check(args.source, args.plan)
        elif args.command == 'build': result = build_one(args.source, args.plan, args.out)
        elif args.command == 'batch': result = batch(args.jobs, args.report)
        elif args.command == 'review': result = {'status': 'verified', **record_reviews(args.manifest, args.reviews)}
        elif args.command == 'verify': result = {'status': 'verified', **verify(args.manifest)}
        else:
            proof = export_linked(args.records, args.out, args.records_out, args.manifests)
            result = {'status': 'exported', 'images': len(proof['images']), 'manifest': str((args.out/'delivery-manifest.json').resolve())}
    except (ValueError, KeyError, OSError, TypeError) as e:
        print(json.dumps({'status': 'failed', 'error': str(e)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 2 if result['status'] in ('failed', 'needs_source_review') else 0


if __name__ == '__main__':
    sys.exit(main())
