"""Completeness regressions: full papers, two rulers and review tied to pixels."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image, ImageDraw, ImageOps
import numpy as np

from complete_photos import build, render, verify, record_reviews, PARTS
from photo_groups import digest
from photo_delivery import export as deliver, verify as verify_delivery
from scale_overlay import extract_seeded_ink


def fixture(root, shared=False, orientation=1, rotation=0):
    im = Image.new('RGB', (720, 500), (193, 190, 183))
    d = ImageDraw.Draw(im)
    d.rectangle((40, 55, 195, 130), fill='#e5dfd0', outline='black', width=2)
    d.text((55, 75), 'CAT-1', fill='black')
    d.ellipse((240, 85, 320, 160), fill='#5c3720')
    d.line((245, 90, 225, 40), fill='#5c3720', width=2)  # long appendage
    d.rectangle((350, 50, 540, 245), fill='#e0dbcf', outline='black', width=2)
    d.text((365, 75), 'collection: complete paper', fill='black')
    scale = [35, 320 if shared else 190, 205, 415 if shared else 285]
    d.rectangle(scale, fill='#dfdbd0')
    d.text((scale[0]+20, scale[1]+15), '10 mm', fill='black')
    d.line((scale[0]+15, scale[1]+65, scale[2]-15, scale[1]+65), fill='black', width=4)
    for x in (scale[0]+15, scale[0]+85, scale[2]-15):
        d.line((x, scale[1]+56, x, scale[1]+74), fill='black', width=3)
    if rotation:
        im = im.transpose(Image.Transpose.ROTATE_180)
    source = root/'source.png'
    exif = Image.Exif(); exif[274] = orientation
    im.save(source, exif=exif)
    scale_info = {'box': scale, 'ink_box': [scale[0]+8, scale[1]+8, scale[2]-8, scale[3]-8], 'label_text': '10 mm'}
    group = {'specimen_number': 'CAT-1', 'group_box': [15, 15, 575, 300],
             'parts': {'catalogue': [35, 50, 200, 135], 'specimen': [218, 32, 330, 170],
                       'collection': [345, 45, 545, 250]}, 'pairing_reviewed': True,
             'source_review': {**{key: True for key in PARTS[:-1]}, 'reviewer': 'fixture source review',
                               'note': 'Full paper edges, appendage and ruler ticks checked.'}}
    plan = {'source_sha256': digest(source), 'exif_display_size': [720, 500],
            'reading_rotation': rotation, 'oriented_size': [720, 500],
            'coordinate_space': 'oriented_native_pixels', 'expected_groups': 1, 'groups': [group]}
    if shared:
        plan['native_scale'] = scale_info
    else:
        group['native_scale'] = scale_info
    p = root/'plan.json'; p.write_text(json.dumps(plan))
    return source, p, plan


def approve(manifest, root):
    proof = json.loads(manifest.read_text())
    checks = [{'specimen_number': e['specimen_number'], 'image_sha256': e['sha256'],
               'reviewer': 'fixture final review', 'note': 'All five complete parts checked.',
               **{role: True for role in PARTS}} for e in proof['images']]
    review = root/'reviews.json'; review.write_text(json.dumps(checks))
    return record_reviews(manifest, review)


class CompletePhotoTests(unittest.TestCase):
    def test_nearby_background_in_a_narrow_gap_avoids_a_distant_colour_patch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source, _, plan = fixture(root)
            original = np.asarray(Image.open(source)).copy()
            plain = np.all(original == (193, 190, 183), axis=2)
            gradient = np.repeat(np.uint8(150+np.arange(500)*100/499)[:, None], 720, axis=1)
            for channel in range(3): original[:, :, channel][plain] = gradient[plain]
            image = Image.fromarray(original); ImageDraw.Draw(image).rectangle((350, 17, 549, 31), fill='black')
            group = plan['groups'][0]
            group['group_box'] = [15, 15, 560, 300]
            group['parts']['specimen'] = [20, 20, 345, 280]
            group['cleanup'] = {'reviewed': True, 'note': 'Foreign paper fragment in the upper gap',
                                'exclude_boxes': [[350, 17, 550, 32]]}
            raw, final, _, _, proof = render(image, group, group['native_scale'])
            x, y = proof['base_box'][:2]
            pixel = final.getpixel((425-15+x, 25-15+y))
            self.assertLess(max(abs(v-155) for v in pixel), 12)
            self.assertEqual(final.getpixel((250-15+x, 100-15+y)), image.getpixel((250, 100)))

    def test_seed_selection_retains_full_glyphs_and_end_ticks_but_removes_pin_and_edge(self):
        source = Image.new('RGB', (320, 190), (205, 201, 193)); d = ImageDraw.Draw(source)
        d.line((80, 16, 80, 75), fill='black', width=6)
        d.text((110, 45), '0 mm', fill='black')
        d.line((45, 135, 270, 135), fill='black', width=5)
        d.line((45, 120, 45, 152), fill='black', width=5)
        d.line((270, 120, 270, 152), fill='black', width=5)
        d.line((12, 8, 30, 25), fill='black', width=4)  # separate fixing pin
        d.line((310, 10, 310, 180), fill='black', width=4)  # paper edge
        asset, meta = extract_seeded_ink(source, [[77, 35, 165, 70], [65, 130, 250, 140]])
        alpha = Image.new('L', source.size); alpha.paste(asset.getchannel('A'), tuple(meta['ink_box'][:2]))
        self.assertGreater(alpha.getpixel((80, 16)), 0)  # outside seed, preserved whole
        self.assertGreater(alpha.getpixel((45, 151)), 0)
        self.assertGreater(alpha.getpixel((270, 151)), 0)
        self.assertEqual(alpha.getpixel((20, 16)), 0)
        self.assertEqual(alpha.getpixel((310, 60)), 0)
        self.assertEqual(asset.convert('RGB').tobytes(), source.crop(meta['ink_box']).tobytes())

    def test_native_visible_still_adds_synthetic_and_keeps_every_source_pixel(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source, p, plan = fixture(root)
            proof = build(source, p, root/'staged'); e = proof['images'][0]
            raw = Image.open(e['raw_path']); final = Image.open(e['path'])
            original = Image.open(source)
            self.assertEqual(raw.tobytes(), original.crop(plan['groups'][0]['group_box']).tobytes())
            shift_x, shift_y = e['composition']['base_box'][:2]
            outer = plan['groups'][0]['group_box']
            for b in [*plan['groups'][0]['parts'].values(), plan['groups'][0]['native_scale']['box']]:
                dest = [b[0]-outer[0]+shift_x, b[1]-outer[1]+shift_y,
                        b[2]-outer[0]+shift_x, b[3]-outer[1]+shift_y]
                self.assertEqual(final.crop(dest).tobytes(), original.crop(b).tobytes())
            self.assertEqual(e['composition']['native_scale_method'], 'visible_in_continuous_source_group')
            self.assertNotEqual(e['composition']['synthetic_scale_box'], e['composition']['native_scale_box'])
            self.assertEqual(e['composition']['scale_factor'], 1)
            self.assertEqual(approve(root/'staged/complete-manifest.json', root)['verified_images'], 1)

    def test_distant_shared_scale_keeps_whole_paper_and_separate_ink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source, p, plan = fixture(root, shared=True)
            proof = build(source, p, root/'staged'); e = proof['images'][0]
            native = Image.open(source).crop(plan['native_scale']['box'])
            final = Image.open(e['path'])
            self.assertEqual(final.crop(e['composition']['native_scale_box']).tobytes(), native.tobytes())
            self.assertNotEqual(e['composition']['native_scale_box'], e['composition']['synthetic_scale_box'])
            approve(root/'staged/complete-manifest.json', root)

    def test_pixels_only_never_grants_visual_completeness_and_wrong_hash_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source, p, _ = fixture(root)
            build(source, p, root/'staged'); manifest = root/'staged/complete-manifest.json'
            self.assertFalse(verify(manifest, require_review=False)['visual_completeness_reviewed'])
            with self.assertRaisesRegex(ValueError, 'final visual review'):
                verify(manifest)
            approve(manifest, root)
            proof = json.loads(manifest.read_text()); proof['images'][0]['final_review']['image_sha256'] = 'stale'
            manifest.write_text(json.dumps(proof))
            with self.assertRaisesRegex(ValueError, 'image hash'):
                verify(manifest)

    def test_missing_or_clipped_whole_paper_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source, p, plan = fixture(root)
            for change in ('missing', 'clip', 'cleanup'):
                bad = deepcopy(plan)
                if change == 'missing':
                    bad['groups'][0]['parts'].pop('collection')
                elif change == 'clip':
                    bad['groups'][0]['group_box'][2] = 500
                else:
                    bad['groups'][0]['cleanup'] = {'reviewed': True, 'note': 'Bad mask would erase label',
                                                  'exclude_boxes': [[350, 60, 400, 100]]}
                p.write_text(json.dumps(bad))
                with self.assertRaises(ValueError):
                    build(source, p, root/change)
                self.assertFalse((root/change).exists())

    def test_exif_and_additional_reading_rotation_use_one_explicit_coordinate_space(self):
        for orientation, rotation in ((3, 0), (1, 180)):
            with self.subTest(orientation=orientation), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                # Rotate stored pixels, then undo through EXIF OR reading rotation.
                source, p, plan = fixture(root, rotation=180)
                im = Image.open(source); exif = Image.Exif(); exif[274] = orientation
                im.save(source, exif=exif)
                plan.update(source_sha256=digest(source), reading_rotation=rotation)
                p.write_text(json.dumps(plan))
                build(source, p, root/'staged')
                approve(root/'staged/complete-manifest.json', root)

    def test_delivery_requires_reviewed_complete_source_and_correct_pairing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source, p, _ = fixture(root)
            proof = build(source, p, root/'staged'); manifest = root/'staged/complete-manifest.json'
            approve(manifest, root)
            batch = {'sources': {'p1': {'path': str(source), 'sha256': digest(source)}},
                     'records': [{'source': 'p1', 'values': {'标本号': 'CAT-1', '海拔': 733},
                                  'image_provenance': {'original': str(source), 'source_sha256': digest(source),
                                                       'complete_photo': {'path': proof['images'][0]['path']}}}]}
            records = root/'records.json'; records.write_text(json.dumps(batch))
            deliver(records, root/'images', root/'records-out.json', [manifest], require_complete=True)
            self.assertEqual(verify_delivery(root/'images/delivery-manifest.json')['verified_images'], 1)
            batch['records'][0]['image_provenance'].pop('complete_photo')
            records.write_text(json.dumps(batch))
            with self.assertRaisesRegex(ValueError, 'requires the complete'):
                deliver(records, root/'bad-images', root/'bad-records.json', [manifest], require_complete=True)

    def test_changing_delivered_pixels_or_omitting_one_reviewed_part_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source, p, _ = fixture(root)
            proof = build(source, p, root/'staged'); manifest = root/'staged/complete-manifest.json'
            approve(manifest, root)
            data = json.loads(manifest.read_text())
            data['images'][0]['final_review']['collection'] = False
            manifest.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, 'all five'):
                verify(manifest)
            data['images'][0]['final_review']['collection'] = True
            manifest.write_text(json.dumps(data))
            path = Path(proof['images'][0]['path'])
            im = Image.open(path); im.putpixel((60, 70), (255, 0, 0)); im.save(path)
            with self.assertRaisesRegex(ValueError, 'replayed source pixels'):
                verify(manifest)


if __name__ == '__main__':
    unittest.main()
