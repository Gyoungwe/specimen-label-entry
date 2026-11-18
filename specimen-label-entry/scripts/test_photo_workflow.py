import json
from pathlib import Path
import tempfile
import unittest
from PIL import Image

from photo_workflow import prepare, check, build_one, batch
from complete_photos import record_reviews
from test_complete_photos import fixture


class PhotoWorkflowTests(unittest.TestCase):
    def test_native_coordinate_prepare_respects_exif_and_reading_turn_without_approving(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); photo = root/'non-square.png'
            im = Image.new('RGB', (800, 500)); exif = Image.Exif(); exif[274] = 6; im.save(photo, exif=exif)
            result = prepare(photo, root/'work', 90, ['CAT-1'])
            self.assertEqual(result['oriented_size'], [800, 500])
            plan = json.loads((root/'work/plan.json').read_text())
            self.assertEqual(plan['exif_display_size'], [500, 800])
            self.assertFalse(plan['groups'][0]['pairing_reviewed'])
            issues = check(photo, root/'work/plan.json')['issues']
            self.assertEqual({i['part'] for i in issues if i['code'] == 'MISSING_REGION'}, {'specimen', 'catalogue', 'collection'})

    def test_build_generates_viewable_guides_and_hash_bound_false_review_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source, plan, data = fixture(root)
            data['groups'][0]['cleanup'] = {'reviewed': True, 'note': 'Proposed empty corner',
                                           'exclude_boxes': [[560, 20, 570, 30]]}
            plan.write_text(json.dumps(data))
            self.assertEqual(len(check(source, plan)['cleanup_notes']), 1)
            data['groups'][0].pop('cleanup'); plan.write_text(json.dumps(data))
            result = build_one(source, plan, root/'staged')
            self.assertEqual(result['status'], 'needs_final_visual_review')
            self.assertTrue(all(Path(p).is_file() for p in result['contacts']))
            template = json.loads(Path(result['review_template']).read_text())
            self.assertFalse(template[0]['synthetic_scale'])
            with self.assertRaisesRegex(ValueError, 'all five'):
                record_reviews(root/'staged/complete-manifest.json', Path(result['review_template']))

    def test_batch_paths_follow_job_file_and_a_failed_job_never_approves_the_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source, plan, data = fixture(root)
            bad = json.loads(json.dumps(data)); bad['groups'][0]['parts']['collection'] = None
            (root/'bad-plan.json').write_text(json.dumps(bad))
            jobs = root/'jobs.json'; jobs.write_text(json.dumps([
                {'source': 'source.png', 'plan': 'plan.json', 'out': 'good'},
                {'source': 'source.png', 'plan': 'bad-plan.json', 'out': 'bad'}]))
            result = batch(jobs, root/'reports/batch.json')
            self.assertEqual(result['failed_jobs'], 1)
            self.assertFalse(result['delivery_ready'])
            self.assertEqual(result['jobs'][0]['status'], 'needs_final_visual_review')
            self.assertFalse((root/'bad').exists())
            with self.assertRaisesRegex(ValueError, 'separately'):
                batch(jobs, jobs)
            with self.assertRaisesRegex(ValueError, 'outside'):
                batch(jobs, root/'good/complete-manifest.json')

    def test_existing_work_is_retained(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source, plan, _ = fixture(root)
            result = prepare(source, root/'work')
            previous = (root/'work/plan.json').read_bytes()
            with self.assertRaisesRegex(ValueError, 'fresh'):
                prepare(source, root/'work')
            self.assertEqual((root/'work/plan.json').read_bytes(), previous)


if __name__ == '__main__':
    unittest.main()
