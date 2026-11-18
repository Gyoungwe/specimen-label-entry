"""A finished photo has one ruler; native ruler evidence stays separate."""
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image, ImageDraw

from complete_photos import build, verify, record_reviews
from photo_workflow import prepare, build_one
from test_complete_photos import fixture
from photo_groups import digest


class SingleRulerTests(unittest.TestCase):
    def test_review_of_a_previous_same_named_output_cannot_approve_the_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source,path,plan=fixture(root,shared=True)
            plan['ruler_policy']='single';path.write_text(json.dumps(plan))
            result=build_one(source,path,root/'staged');review_path=Path(result['review_template'])
            checks=json.loads(review_path.read_text());self.assertTrue(Path(checks[0]['image_path']).is_file())
            checks[0].update(image_path=str(root/'previous/images/CAT-1.png'),reviewer='fixture reviewer',
                             note='Wrong same-named output',catalogue=True,specimen=True,collection=True,scale=True)
            review_path.write_text(json.dumps(checks))
            with self.assertRaisesRegex(ValueError,'different image path'):
                record_reviews(root/'staged/complete-manifest.json',review_path)

    def test_null_cleanup_in_existing_plans_means_untouched_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source,path,plan=fixture(root,shared=True)
            plan['ruler_policy']='single';plan['groups'][0]['cleanup']=None
            path.write_text(json.dumps(plan))
            self.assertEqual(build_one(source,path,root/'staged')['status'],'needs_final_visual_review')

    def stage(self, root, shared=False, policy='single'):
        source, path, plan = fixture(root, shared=shared)
        if policy is None:
            plan.pop('ruler_policy', None)
        else:
            plan['ruler_policy'] = policy
        path.write_text(json.dumps(plan))
        proof = build(source, path, root/'staged')
        return source, proof, root/'staged/complete-manifest.json'

    def test_visible_native_ruler_does_not_add_another_or_extend_the_canvas(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); _, proof, manifest = self.stage(root)
            entry = proof['images'][0]
            self.assertEqual(entry['composition']['displayed_scale_count'], 1)
            self.assertEqual(entry['composition']['displayed_scale_kind'], 'native')
            self.assertIsNone(entry['composition']['synthetic_scale_box'])
            self.assertEqual(Image.open(entry['path']).tobytes(), Image.open(entry['raw_path']).tobytes())
            self.assertTrue(Path(entry['ink_path']).exists())
            self.assertFalse(verify(manifest, require_review=False)['visual_completeness_reviewed'])

    def test_remote_ruler_paper_is_only_a_separate_asset(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source, proof, _ = self.stage(root, shared=True)
            entry = proof['images'][0]; composition = entry['composition']
            self.assertEqual(composition['displayed_scale_count'], 1)
            self.assertEqual(composition['displayed_scale_kind'], 'synthetic')
            self.assertIsNone(composition['native_scale_box'])
            self.assertIsNotNone(composition['synthetic_scale_box'])
            self.assertEqual(composition['scale_factor'], 1)
            with Image.open(entry['raw_path']) as raw, Image.open(entry['path']) as final:
                self.assertEqual(raw.size, final.size)  # fixture has a safe blank area
            with Image.open(entry['native_scale_path']) as native, Image.open(source) as original:
                self.assertEqual(native.tobytes(), original.crop(proof['plan']['native_scale']['box']).tobytes())

    def test_new_prepare_and_build_without_policy_default_to_single(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source, proof, _ = self.stage(root, policy=None)
            self.assertEqual(proof['plan']['ruler_policy'], 'single')
            prepare(source, root/'prepared')
            self.assertEqual(json.loads((root/'prepared/plan.json').read_text())['ruler_policy'], 'single')

    def test_review_template_requires_the_single_visible_ruler_and_binds_pixels(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source, path, plan = fixture(root, shared=True)
            plan['ruler_policy'] = 'single'; path.write_text(json.dumps(plan))
            result = build_one(source, path, root/'staged')
            reviews = json.loads(Path(result['review_template']).read_text())
            self.assertIn('scale', reviews[0]); self.assertFalse(reviews[0]['scale'])
            self.assertNotIn('native_scale', reviews[0])
            manifest = root/'staged/complete-manifest.json'
            with self.assertRaises(ValueError):record_reviews(manifest, Path(result['review_template']))
            reviews[0].update(reviewer='fixture reviewer', note='Whole subjects and exactly one ruler checked.',
                              catalogue=True, specimen=True, collection=True, scale=True)
            Path(result['review_template']).write_text(json.dumps(reviews))
            self.assertEqual(record_reviews(manifest, Path(result['review_template']))['verified_images'], 1)

    def test_partial_original_ruler_cannot_silently_get_a_second_synthetic_ruler(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source, path, plan = fixture(root, shared=True)
            plan['ruler_policy'] = 'single'; plan['groups'][0]['group_box'][3] = 350
            path.write_text(json.dumps(plan))
            with self.assertRaisesRegex(ValueError, 'partial.*ruler'):
                build(source, path, root/'staged')
            self.assertFalse((root/'staged').exists())

    def test_legacy_two_ruler_manifest_still_replays_without_a_policy_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); _, proof, manifest = self.stage(root, policy='both')
            proof['plan'].pop('ruler_policy')
            manifest.write_text(json.dumps(proof))
            self.assertEqual(verify(manifest, require_review=False)['verified_images'], 1)

    def test_a_second_known_source_ruler_is_rejected_before_writing_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source, path, plan = fixture(root)
            with Image.open(source) as original: image = original.copy()
            draw = ImageDraw.Draw(image)
            draw.text((375,260), '10 mm', fill='black');draw.line((370,280,480,280), fill='black', width=3)
            image.save(source)
            plan.update(source_sha256=digest(source),ruler_policy='single',
                        native_scale={'box':[360,255,490,290],'ink_box':[360,255,490,290],'label_text':'10 mm'})
            path.write_text(json.dumps(plan))
            with self.assertRaisesRegex(ValueError, 'another native ruler'):
                build(source, path, root/'staged')
            self.assertFalse((root/'staged').exists())

    def test_a_reviewed_foreign_ruler_exclusion_preserves_the_target_ruler(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source, path, plan = fixture(root)
            with Image.open(source) as original: image = original.copy()
            draw = ImageDraw.Draw(image);draw.line((370,280,480,280), fill='black', width=3)
            image.save(source)
            plan.update(source_sha256=digest(source),ruler_policy='single',
                        native_scale={'box':[360,255,490,290],'ink_box':[360,255,490,290],'label_text':'10 mm'})
            plan['groups'][0]['cleanup']={'reviewed':True,'note':'Remove only the second foreign ruler',
                                         'exclude_boxes':[[357,252,493,293]]}
            path.write_text(json.dumps(plan));proof=build(source,path,root/'staged')
            entry=proof['images'][0];box=entry['composition']['native_scale_box']
            with Image.open(entry['path']) as final, Image.open(source) as original:
                self.assertEqual(final.crop(box).tobytes(),original.crop(plan['groups'][0]['native_scale']['box']).tobytes())
                self.assertGreater(min(final.getpixel((400-15,280-15))),100)

    def test_separate_appendage_protection_survives_neighbour_background_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source, path, plan = fixture(root)
            plan['ruler_policy']='single';g=plan['groups'][0]
            g['parts']['specimen']=[238,82,330,170]
            g['parts']['specimen_antenna']=[218,32,250,100]
            g['cleanup']={'reviewed':True,'note':'Foreign mark beside the fully protected antenna',
                          'exclude_boxes':[[205,25,215,100]]}
            path.write_text(json.dumps(plan));proof=build(source,path,root/'staged');e=proof['images'][0]
            with Image.open(e['path']) as final, Image.open(source) as original:
                b=g['parts']['specimen_antenna'];target=[b[0]-15,b[1]-15,b[2]-15,b[3]-15]
                self.assertEqual(final.crop(target).tobytes(),original.crop(b).tobytes())
            g['parts']['specimen_antenna']=[0,0,250,100];path.write_text(json.dumps(plan))
            with self.assertRaisesRegex(ValueError,'additional protected'):
                build(source,path,root/'bad')
            self.assertFalse((root/'bad').exists())


if __name__ == '__main__':unittest.main()
