#!/usr/bin/env python3
"""Build and audit photographs with complete labels, specimen and both ruler views.

Source coordinates are native pixels AFTER EXIF orientation and the declared
reading rotation. Semantic completeness is a visual review, bound to image hashes;
pixel replay proves preservation of that reviewed evidence, not semantic detection.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import warnings

from PIL import Image
import numpy as np

from photo_groups import digest, open_source
from scale_overlay import extract_ink, extract_seeded_ink, blank_edge_samples, apply_asset, quiet_position
from precision_masks import clean_background

PARTS = ('catalogue', 'specimen', 'collection', 'native_scale', 'synthetic_scale')
SOURCE_PARTS = PARTS[:-1]
TRANSPOSE = {90: Image.Transpose.ROTATE_90, 180: Image.Transpose.ROTATE_180,
             270: Image.Transpose.ROTATE_270}


def checked_box(value, size):
    if (not isinstance(value, list) or len(value) != 4 or
            any(isinstance(x, bool) or not isinstance(x, int) for x in value)):
        raise ValueError('Use four integer native-pixel coordinates, not OCR/preview coordinates')
    x0, y0, x1, y1 = value
    if not (0 <= x0 < x1 <= size[0] and 0 <= y0 < y1 <= size[1]):
        raise ValueError(f'Out-of-bounds native box: {value}')
    return value


def contains(outer, inner, margin=0):
    return (outer[0]+margin <= inner[0] and outer[1]+margin <= inner[1] and
            inner[2]+margin <= outer[2] and inner[3]+margin <= outer[3])


def oriented_source(source, plan):
    if digest(source) != plan.get('source_sha256'):
        raise ValueError('Source photograph changed; review the source again')
    im, orientation = open_source(source)
    if list(im.size) != plan.get('exif_display_size'):
        raise ValueError('EXIF display dimensions differ from the reviewed plan')
    rotation = plan.get('reading_rotation', 0)
    if isinstance(rotation, bool) or rotation not in (0, 90, 180, 270):
        raise ValueError('Use an explicit 0/90/180/270 degree reading rotation')
    if rotation:
        im = im.transpose(TRANSPOSE[rotation])
    if plan.get('coordinate_space') != 'oriented_native_pixels' or list(im.size) != plan.get('oriented_size'):
        raise ValueError('Coordinates must refer to the EXIF-oriented, reading-rotated native image')
    return im, orientation


def validate_group(group, image, shared_scale):
    sid = group.get('specimen_number')
    if not isinstance(sid, str) or not re.fullmatch(r'[A-Za-z0-9()._-]+', sid):
        raise ValueError('Read each actual catalogue ID and use safe filename characters')
    outer = checked_box(group.get('group_box'), image.size)
    parts = group.get('parts', {})
    for role in ('catalogue', 'specimen', 'collection'):
        box = checked_box(parts.get(role), image.size)
        if not contains(outer, box, margin=2):
            raise ValueError(f'{sid}: group crop clips or touches the reviewed {role} region; expand it')
    # A number string is not the whole number-label paper. The two paper roles
    # and the specimen (including appendages) are independently reviewed.
    scale = group.get('native_scale') or shared_scale
    if not isinstance(scale, dict) or not str(scale.get('label_text', '')).strip():
        raise ValueError(f'{sid}: a complete same-photograph native ruler is required')
    paper = checked_box(scale.get('box'), image.size)
    ink = checked_box(scale.get('ink_box'), image.size)
    if not contains(paper, ink):
        raise ValueError('The digits, unit and all ticks must lie inside the full ruler-paper crop')
    if group.get('pairing_reviewed') is not True:
        raise ValueError(f'{sid}: review the catalogue/specimen/collection association on the source')
    review = group.get('source_review', {})
    if not isinstance(review.get('reviewer'), str) or not review['reviewer'].strip():
        raise ValueError('Name the source visual reviewer (agent or human)')
    if any(review.get(role) is not True for role in SOURCE_PARTS):
        raise ValueError(f'{sid}: visually confirm the complete papers, specimen and native ruler')
    if not isinstance(review.get('note'), str) or not review['note'].strip():
        raise ValueError('Record what was checked on the source, including paper edges and appendages')
    if group.get('cleanup'):
        cleanup = group['cleanup']
        if cleanup.get('reviewed') is not True or not str(cleanup.get('note', '')).strip():
            raise ValueError(f'{sid}: review background exclusions before building')
        protected = dict(parts)
        if contains(outer, paper): protected['native_scale'] = paper
        for removed in cleanup.get('exclude_boxes', []):
            checked_box(removed, image.size)
            if not contains(outer, removed):
                raise ValueError(f'{sid}: cleanup region lies outside the group crop')
            for role, kept in protected.items():
                if removed[0] < kept[2] and kept[0] < removed[2] and removed[1] < kept[3] and kept[1] < removed[3]:
                    raise ValueError(f'{sid}: cleanup intersects complete {role}; inspect the source and refine the mask')
    return scale


def render(image, group, scale):
    """Keep the continuous source group intact; add same-photo ruler evidence 1:1."""
    outer = group['group_box']
    raw = image.crop(outer)
    base = raw
    cleanup_proof = None
    if group.get('cleanup'):
        cleanup = group['cleanup']
        if cleanup.get('reviewed') is not True or not str(cleanup.get('note', '')).strip():
            raise ValueError('Review each neighbouring fragment before masking the display copy')
        def local(box):
            return [box[0]-outer[0], box[1]-outer[1], box[2]-outer[0], box[3]-outer[1]]
        def polygon(box):
            x0, y0, x1, y1 = box
            return [[x0, y0], [x1-1, y0], [x1-1, y1-1], [x0, y1-1]]
        excluded = [checked_box(local(b), raw.size) for b in cleanup['exclude_boxes']]
        protected = [local(b) for b in group['parts'].values()]
        if contains(outer, scale['box']):
            protected.append(local(scale['box']))
        for a in excluded:
            for b in protected:
                if a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]:
                    raise ValueError('Cleanup would intersect a protected complete component; refine the mask')
        samples = cleanup_background_samples(raw, protected+excluded, excluded)
        if not samples:
            raise ValueError('Choose a reviewed blank background patch for cleanup')
        mask = {'coordinate_space': 'crop_pixels', 'source_sha256': hashlib.sha256(raw.tobytes()).hexdigest(),
                'image_size': list(raw.size), 'reviewed': True,
                'exclude_polygons': [polygon(b) for b in excluded],
                'protected_polygons': [polygon(b) for b in protected],
                'background_samples': samples, 'protection_margin': 2, 'feather_radius': 10}
        base, cleanup_proof = clean_background(raw, mask, mask['source_sha256'])
    native, ink, extraction = ruler_asset(image, scale)
    native_in_group = contains(outer, scale['box'])
    margin = 32
    # Always include a synthetic ruler, even when the original is already visible.
    # A distant shared ruler is also shown as its FULL paper crop, not ink alone.
    strip_width = ink.width + (native.width+margin if not native_in_group else 0)
    strip_height = max(ink.height, native.height if not native_in_group else 0)
    if native_in_group:
        protected = [[b[0]-outer[0], b[1]-outer[1], b[2]-outer[0], b[3]-outer[1]]
                     for b in [*group['parts'].values(), scale['box']]]
        placement = quiet_position(base, ink, protected, margin=24)
        if placement is not None:
            result = apply_asset(base, ink, placement)
            local = [scale['box'][0]-outer[0], scale['box'][1]-outer[1],
                     scale['box'][2]-outer[0], scale['box'][3]-outer[1]]
            return raw, result, native, ink, {
                'base_box': [0, 0, base.width, base.height], 'native_scale_box': local,
                'native_scale_method': 'visible_in_continuous_source_group',
                'synthetic_scale_box': placement, 'scale_factor': 1,
                'ink_extraction': extraction, 'cleanup': cleanup_proof,
                'processing': 'integer crop/rotation; protected components unchanged; synthetic ruler in reviewed blank area at 1:1'}
    canvas, base_box, strip = ruler_footer(base, strip_width, strip_height, margin)
    y = strip[1]
    native_output = None
    if native_in_group:
        dx, dy = base_box[0]-outer[0], base_box[1]-outer[1]
        native_output = [scale['box'][0]+dx, scale['box'][1]+dy,
                         scale['box'][2]+dx, scale['box'][3]+dy]
        native_method = 'visible_in_continuous_source_group'
    else:
        native_output = [strip[0], y, strip[0]+native.width, y+native.height]
        canvas.paste(native, tuple(native_output[:2]))
        native_method = 'full_paper_reference_copied_from_same_photograph'
    ink_output = [strip[2]-ink.width, y, strip[2], y+ink.height]
    canvas = apply_asset(canvas, ink, ink_output)
    proof = {'base_box': base_box, 'native_scale_box': native_output,
             'native_scale_method': native_method, 'synthetic_scale_box': ink_output,
             'scale_factor': 1, 'ink_extraction': extraction,
             'cleanup': cleanup_proof,
             'processing': 'integer crop/rotation; native papers and specimen unchanged; same-photo ruler at 1:1'}
    return raw, canvas, native, ink, proof


def cleanup_background_samples(base, occupied, removed=()):
    """Find quiet source desk pixels outside every protected or removed region."""
    side = min(24, base.width, base.height)
    grey = np.asarray(base.convert('L'), dtype=np.float32)
    candidates = []; boxes = set()
    for y in sorted(set([0, base.height-side, *range(0, base.height-side+1, side)])):
        for x in sorted(set([0, base.width-side, *range(0, base.width-side+1, side)])):
            boxes.add((x, y, x+side, y+side))
    # A narrow gap next to a foreign label may be the best background evidence.
    # Include small local patches instead of choosing only distant flat corners.
    for left, top, right, bottom in removed:
        for distance in (3, 12, 24, 48):
            for x in range(left, right+1, 16):
                boxes.add((x, top-distance-8, x+8, top-distance))
                boxes.add((x, bottom+distance, x+8, bottom+distance+8))
            for y in range(top, bottom+1, 16):
                boxes.add((left-distance-8, y, left-distance, y+8))
                boxes.add((right+distance, y, right+distance+8, y+8))
    for box in sorted(boxes):
        x, y, right, bottom = box; b = list(box)
        if not (0 <= x < right <= base.width and 0 <= y < bottom <= base.height): continue
        if any(b[0] < a[2] and a[0] < b[2] and b[1] < a[3] and a[1] < b[3] for a in occupied):
            continue
        patch = grey[y:bottom, x:right]
        median = float(np.median(patch))
        if median < 80 or np.mean(patch < median-25) > .01:
            continue
        spread = float(np.percentile(patch, 90)-np.percentile(patch, 10))
        if spread < 25:
            cx, cy = (x+right)/2, (y+bottom)/2
            distance = min((max(a[0]-cx, 0, cx-a[2])**2 + max(a[1]-cy, 0, cy-a[3])**2
                            for a in removed), default=0)
            candidates.append((distance, spread, b))
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    return [b for _, _, b in candidates[:4]]


def ruler_asset(image, scale):
    native = image.crop(scale['box'])
    ink_source = image.crop(scale['ink_box'])
    exclusions = scale.get('ink_exclude_boxes', [])
    mask = None
    sha = hashlib.sha256(ink_source.tobytes()).hexdigest()
    if exclusions:
        if scale.get('ink_mask_reviewed') is not True or not str(scale.get('ink_mask_note', '')).strip():
            raise ValueError('Review ruler paper-edge/pin exclusions against all digits and ticks')
        x0, y0, x1, y1 = scale['ink_box']
        polygons = []
        for box in exclusions:
            checked_box(box, image.size)
            left, top, right, bottom = max(box[0], x0), max(box[1], y0), min(box[2], x1), min(box[3], y1)
            if left < right and top < bottom:
                polygons.append([[left-x0, top-y0], [right-x0-1, top-y0],
                                 [right-x0-1, bottom-y0-1], [left-x0, bottom-y0-1]])
        if not polygons:
            raise ValueError('Ruler exclusion boxes do not intersect the ink crop')
        mask = {'coordinate_space': 'crop_pixels', 'image_size': list(ink_source.size),
                'source_sha256': sha, 'reviewed': True, 'exclude_polygons': polygons}
    seeds = scale.get('ink_seed_boxes')
    if seeds:
        if scale.get('ink_seeds_reviewed') is not True:
            raise ValueError('Review digit/unit/tick seed regions and the extracted ruler')
        left, top = scale['ink_box'][:2]
        local_seeds = [[b[0]-left, b[1]-top, b[2]-left, b[3]-top] for b in seeds]
        drops = [[p[0]-left, p[1]-top] for p in scale.get('ink_drop_points', [])]
        if drops and (scale.get('ink_mask_reviewed') is not True or not str(scale.get('ink_mask_note', '')).strip()):
            raise ValueError('Review every connected artifact exclusion before dropping it')
        ink, extraction = extract_seeded_ink(ink_source, local_seeds, mask_plan=mask, source_sha256=sha, drop_points=drops)
    else:
        ink, extraction = extract_ink(ink_source, mask_plan=mask, source_sha256=sha)
    return native, ink, extraction


def ruler_footer(base, strip_width, strip_height, margin):
    """Extend only the footer using quiet native paper, never stretch edge ink."""
    width = max(base.width, strip_width+2*margin)
    height = base.height+strip_height+2*margin
    boxes = blank_edge_samples(base)
    if not boxes:
        raise ValueError('No quiet paper patch for the ruler extension; expand the reviewed source crop')
    # Estimate the continuation at the actual bottom boundary, not the brightest
    # unrelated corner. Filter dark foreground before estimating any colour line.
    rgb = np.asarray(base.convert('RGB'), dtype=np.float32)
    band = rgb[-min(64, base.height):]
    grey = band.mean(2)
    light = np.percentile(grey, 75)
    quiet = grey > max(25, light-35)
    filtered = np.where(quiet[:, :, None], band, np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)
        line = np.nanmedian(filtered, axis=0)
    good = np.all(np.isfinite(line), axis=1)
    if not np.any(good):
        raise ValueError('Bottom edge has no usable paper; expand the reviewed crop')
    for channel in range(3):
        line[:, channel] = np.interp(np.arange(base.width), np.flatnonzero(good), line[good, channel])
    # A fitted colour gradient prevents local dirt/shadows from becoming vertical
    # streaks across the extension. Texture is independently sampled native grain.
    x_values = np.linspace(-1, 1, base.width)
    line = np.stack([np.polyval(np.polyfit(x_values, line[:, c], 1), x_values) for c in range(3)], axis=1)
    line = np.clip(line, 0, 255)
    residual = band-line[None, :, :]
    texture = residual[quiet & (np.abs(residual).max(2) < 14)]
    if not len(texture):
        texture = np.zeros((1, 3), dtype=np.float32)
    x = (width-base.width)//2
    colour = np.pad(line, ((x, width-base.width-x), (0, 0)), mode='edge')
    rng = np.random.default_rng(31537)
    grain = texture[rng.integers(0, len(texture), (height-base.height, width))]
    footer = Image.fromarray(np.uint8(np.clip(colour[None, :, :]+grain, 0, 255))).convert(base.mode)
    canvas = Image.fromarray(np.uint8(colour[None, :, :])).resize((width, height)).convert(base.mode)
    canvas.paste(footer, (0, base.height))
    canvas.paste(base, (x, 0))
    return (canvas, [x, 0, x+base.width, base.height],
            [width-margin-strip_width, base.height+margin, width-margin, base.height+margin+strip_height])


def build(source, plan_path, out):
    if out.exists():
        raise ValueError('Use a fresh staging directory; retain previous deliveries')
    plan = json.loads(plan_path.read_text())
    image, orientation = oriented_source(source, plan)
    groups = plan.get('groups', [])
    ids = [g.get('specimen_number') for g in groups]
    if not groups or len(set(ids)) != len(ids) or plan.get('expected_groups') != len(ids):
        raise ValueError('Cover the actual unique ID inventory exactly once, regardless of specimen count')
    scales = [validate_group(g, image, plan.get('native_scale')) for g in groups]
    (out/'raw').mkdir(parents=True)
    (out/'images').mkdir()
    (out/'scales').mkdir()
    proof = {'schema_version': 1, 'kind': 'complete_specimen_photographs',
             'source': str(source.resolve()), 'source_sha256': digest(source),
             'exif_orientation': orientation, 'plan': deepcopy(plan),
             'required_parts': list(PARTS), 'images': [],
             'human_review_pending': True}
    for group, scale in zip(groups, scales):
        sid = group['specimen_number']
        raw, final, native, ink, composition = render(image, group, scale)
        raw_path = out/'raw'/(sid+'.png')
        image_path = out/'images'/(sid+'.png')
        native_path = out/'scales'/(sid+'__native.png')
        ink_path = out/'scales'/(sid+'__ink.png')
        for im, path in ((raw, raw_path), (final, image_path), (native, native_path), (ink, ink_path)):
            im.save(path, format='PNG', compress_level=3)
        proof['images'].append({'specimen_number': sid, 'path': str(image_path.resolve()),
            'sha256': digest(image_path), 'raw_path': str(raw_path.resolve()),
            'native_scale_path': str(native_path.resolve()), 'ink_path': str(ink_path.resolve()),
            'composition': composition, 'final_review': None})
    path = out/'complete-manifest.json'
    path.write_text(json.dumps(proof, ensure_ascii=False, indent=2))
    verify(path, require_review=False)
    return proof


def same_pixels(path, expected):
    with Image.open(path) as actual:
        return (actual.format == 'PNG' and actual.mode == expected.mode and actual.size == expected.size
                and actual.tobytes() == expected.tobytes())


def verify(path, require_review=True):
    proof = json.loads(path.read_text())
    if (proof.get('kind') != 'complete_specimen_photographs' or proof.get('schema_version') != 1 or
            proof.get('required_parts') != list(PARTS)):
        raise ValueError('Expected the five-part photograph completeness manifest')
    source = Path(proof['source'])
    image, orientation = oriented_source(source, proof['plan'])
    if proof['source_sha256'] != digest(source) or proof['exif_orientation'] != orientation:
        raise ValueError('Manifest source/orientation differs')
    groups = proof['plan']['groups']; entries = proof['images']
    ids = [g['specimen_number'] for g in groups]
    if (not ids or len(set(ids)) != len(ids) or len(entries) != len(ids) or
            ids != [e['specimen_number'] for e in entries] or proof['plan']['expected_groups'] != len(ids)):
        raise ValueError('Completeness manifest has missing, duplicate or mispaired IDs')
    for group, entry in zip(groups, entries):
        scale = validate_group(group, image, proof['plan'].get('native_scale'))
        raw, final, native, ink, composition = render(image, group, scale)
        for name, expected in (('raw_path', raw), ('path', final), ('native_scale_path', native), ('ink_path', ink)):
            if not same_pixels(Path(entry[name]), expected):
                raise ValueError(f"{entry['specimen_number']}: {name} differs from replayed source pixels")
        if entry['composition'] != composition or digest(entry['path']) != entry['sha256']:
            raise ValueError('Ruler placement, geometry or image hash differs')
        if require_review:
            check_final_review(entry.get('final_review'), entry)
    return {'verified_images': len(entries), 'required_parts': list(PARTS),
            'visual_completeness_reviewed': require_review, 'scale_factor': 1,
            'human_review_pending': True}


def check_final_review(review, entry):
    if (not isinstance(review, dict) or review.get('image_sha256') != entry['sha256'] or
            any(review.get(role) is not True for role in PARTS) or
            not isinstance(review.get('reviewer'), str) or not review['reviewer'].strip() or
            not isinstance(review.get('note'), str) or not review['note'].strip()):
        raise ValueError(f"{entry['specimen_number']}: final visual review must confirm all five complete parts and bind the image hash")


def record_reviews(manifest, reviews):
    proof = json.loads(manifest.read_text())
    checks = json.loads(reviews.read_text())
    if not isinstance(checks, list):
        raise ValueError('Supply a list of per-ID reviews made after looking at the generated images')
    by_id = {c['specimen_number']: c for c in checks}
    if len(by_id) != len(checks) or set(by_id) != {e['specimen_number'] for e in proof['images']}:
        raise ValueError('Review every generated catalogue image once')
    for entry in proof['images']:
        review = deepcopy(by_id[entry['specimen_number']]); review.pop('specimen_number')
        check_final_review(review, entry)
        entry['final_review'] = review
    # Replay before replacing the pending manifest, preserving it if review fails.
    pending = manifest.with_name('.review-check.json')
    if pending.exists():
        raise ValueError('A review staging file already exists')
    try:
        pending.write_text(json.dumps(proof, ensure_ascii=False, indent=2))
        result = verify(pending)
        pending.replace(manifest)
    finally:
        pending.unlink(missing_ok=True)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    b = sub.add_parser('build'); b.add_argument('source', type=Path); b.add_argument('plan', type=Path)
    b.add_argument('--out', required=True, type=Path)
    v = sub.add_parser('verify'); v.add_argument('manifest', type=Path)
    v.add_argument('--pixels-only', action='store_true', help='Staging check only; does not approve delivery')
    a = sub.add_parser('review'); a.add_argument('manifest', type=Path); a.add_argument('reviews', type=Path)
    args = p.parse_args()
    if args.command == 'build':
        result = build(args.source, args.plan, args.out)
        result = {'staged_images': len(result['images']), 'visual_review_required': True}
    elif args.command == 'review':
        result = record_reviews(args.manifest, args.reviews)
    else:
        result = verify(args.manifest, require_review=not args.pixels_only)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
