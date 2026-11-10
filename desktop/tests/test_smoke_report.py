"""Native acceptance must consume this run's report from the expected frontend."""
import json
from pathlib import Path
import tempfile
import unittest

from scripts.check_smoke_report import verify_smoke_report


class SmokeReportTests(unittest.TestCase):
    def write(self, directory, payload):
        path = Path(directory) / 'result.json'
        path.write_text(json.dumps(payload), encoding='utf-8')
        return path

    def test_accepts_successful_native_report(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(directory, {'success': True, 'message': 'ok', 'frontend': 'WinUI 3'})
            self.assertEqual(verify_smoke_report(path)['message'], 'ok')

    def test_rejects_qml_frontend_report(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(directory, {'success': True, 'message': 'ok',
                                          'warnings': [], 'multiAccount': True})
            with self.assertRaisesRegex(ValueError, 'frontend'):
                verify_smoke_report(path)

    def test_rejects_wrong_frontend_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(directory, {'success': True, 'frontend': 'Qt Quick'})
            with self.assertRaisesRegex(ValueError, "expected 'WinUI 3'"):
                verify_smoke_report(path)

    def test_rejects_failed_report_and_surfaces_its_message(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(directory, {'success': False, 'message': 'failed assertion',
                                          'frontend': 'WinUI 3'})
            with self.assertRaisesRegex(ValueError, 'failed assertion'):
                verify_smoke_report(path)

    def test_rejects_missing_report(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'no report'):
                verify_smoke_report(Path(directory) / 'result.json')

    def test_rejects_unreadable_report(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'result.json'
            path.write_text('{not json', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'unreadable'):
                verify_smoke_report(path)

    def test_empty_frontend_accepts_archived_qml_report(self):
        """The legacy QML regression has its own caller and no frontend marker."""
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(directory, {'success': True, 'message': 'ok'})
            self.assertEqual(verify_smoke_report(path, '')['message'], 'ok')


if __name__ == '__main__':
    unittest.main()
