import json
from pathlib import Path
import tempfile
import unittest

from complete_photos import build
from photo_binding import bind, export_linked
from photo_groups import digest
from test_complete_photos import fixture, approve


class PhotoBindingTests(unittest.TestCase):
    def setup_case(self, root):
        source, plan, _ = fixture(root)
        build(source, plan, root/'staged'); manifest = root/'staged/complete-manifest.json'
        approve(manifest, root)
        data = {'sources': {'p1': {'path': str(source), 'sha256': digest(source)}},
                'records': [{'source': 'p1', 'page': 1, 'issues': ['retain this observation'],
                    'values': {'标本号': 'CAT-1', '海拔': 733, '图像': 'previous.png'},
                    'image_provenance': {'original': str(source), 'group_box': [0, 0, 1, 1],
                                         'group_image': 'wrong-old-crop.png'}}]}
        records = root/'records.json'; records.write_text(json.dumps(data))
        return source, manifest, records, data

    def test_binding_replaces_stale_crop_references_and_preserves_all_specimen_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source, manifest, records, original = self.setup_case(root)
            before = digest(records); result = bind(records, [manifest])
            self.assertEqual(result['records'][0]['values'], original['records'][0]['values'])
            self.assertEqual(result['records'][0]['issues'], ['retain this observation'])
            self.assertNotIn('group_image', result['records'][0]['image_provenance'])
            self.assertIn('complete_photo', result['records'][0]['image_provenance'])
            self.assertEqual(digest(records), before)

    def test_matching_catalogue_does_not_hide_wrong_source_or_repeated_manifests(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source, manifest, records, data = self.setup_case(root)
            with self.assertRaisesRegex(ValueError, 'ambiguous'):
                bind(records, [manifest, manifest])
            other = root/'other-photo.png'; other.write_bytes(source.read_bytes())
            data['sources']['p1']['path'] = str(other); records.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, 'record source'):
                bind(records, [manifest])

    def test_export_links_without_manual_per_record_json_and_retains_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); _, manifest, records, data = self.setup_case(root)
            original = digest(records)
            result = export_linked(records, root/'delivery/images', root/'delivery/records.json', [manifest])
            self.assertEqual(len(result['images']), 1)
            saved = json.loads((root/'delivery/records.json').read_text())
            self.assertEqual(saved['records'][0]['values']['海拔'], 733)
            self.assertEqual(saved['records'][0]['values']['标本号'], 'CAT-1')
            self.assertEqual(digest(records), original)
            self.assertTrue((root/'delivery/records.image-binding.json').is_file())


if __name__ == '__main__':
    unittest.main()
