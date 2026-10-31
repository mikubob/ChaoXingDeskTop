import os
from pathlib import Path
import tempfile
import unittest

from chaoxing_desktop.winui_app import find_frontend


class WinUILauncherTests(unittest.TestCase):
    def test_selects_latest_complete_frontend_and_ignores_incomplete_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, timestamp in [('publish', 100), ('publish-new', 200), ('publish-incomplete', 300)]:
                folder = root / 'winui' / name
                folder.mkdir(parents=True)
                for file in ('ChaoxingDesktop.exe', 'ChaoxingDesktop.dll', 'App.xbf', 'MainWindow.xbf', 'ChaoxingDesktop.pri'):
                    (folder / file).touch()
                os.utime(folder / 'ChaoxingDesktop.dll', (timestamp, timestamp))
            (root / 'winui/publish-incomplete/MainWindow.xbf').unlink()
            self.assertEqual(find_frontend(root), root / 'winui/publish-new/ChaoxingDesktop.exe')

    def test_missing_frontend(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(find_frontend(Path(directory)))
