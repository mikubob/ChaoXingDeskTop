import os
import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

from chaoxing_desktop.winui_app import find_frontend
from chaoxing_desktop import winui_app
from chaoxing_desktop.launch_bootstrap import project_python, relaunch_if_needed


class WinUILauncherTests(unittest.TestCase):
    def test_source_smoke_rejects_failed_report_even_with_zero_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory)
            with patch('sys.argv', ['run.py', '--smoke-test', directory]), \
                    patch.object(winui_app, 'find_frontend', return_value=Path('test.exe')), \
                    patch.object(winui_app.subprocess, 'call', side_effect=lambda *a, **kw:
                        ((destination / 'result.json').write_text('{"success":false,"message":"failed assertion"}', encoding='utf-8') and 0)):
                with self.assertRaisesRegex(RuntimeError, 'failed assertion'):
                    winui_app.main()

    def test_source_smoke_rejects_stale_success_report(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'result.json').write_text('{"success":true}', encoding='utf-8')
            with patch('sys.argv', ['run.py', '--smoke-test', directory]), \
                    patch.object(winui_app, 'find_frontend', return_value=Path('test.exe')), \
                    patch.object(winui_app.subprocess, 'call', return_value=0):
                with self.assertRaises(FileNotFoundError):
                    winui_app.main()

    def test_bootstrap_keeps_current_environment_when_dependencies_exist(self):
        with patch('chaoxing_desktop.launch_bootstrap.importlib.util.find_spec', return_value=object()), \
                patch('chaoxing_desktop.launch_bootstrap.project_python') as find:
            self.assertIsNone(relaunch_if_needed())
            find.assert_not_called()

    def test_global_python_relaunch_preserves_arguments_and_exit_status(self):
        with patch('chaoxing_desktop.launch_bootstrap.importlib.util.find_spec', return_value=None), \
                patch('chaoxing_desktop.launch_bootstrap.project_python', return_value=Path('project-python.exe')), \
                patch('chaoxing_desktop.launch_bootstrap.subprocess.call', return_value=7) as call, \
                patch('sys.argv', ['run.py', '--smoke-test', 'result path']):
            self.assertEqual(relaunch_if_needed(), 7)
            self.assertEqual(call.call_args.args[0][-2:], ['--smoke-test', 'result path'])

    def test_relaunch_on_english_windows_output_encoding(self):
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding='cp1252', errors='strict')
        with redirect_stderr(stream), \
                patch('chaoxing_desktop.launch_bootstrap.importlib.util.find_spec', return_value=None), \
                patch('chaoxing_desktop.launch_bootstrap.project_python', return_value=Path('project-python.exe')), \
                patch('chaoxing_desktop.launch_bootstrap.subprocess.call', return_value=0):
            self.assertEqual(relaunch_if_needed(), 0)
        stream.flush()
        self.assertIn(b'project-python.exe', raw.getvalue())

    def test_relaunch_keeps_diagnostics_out_of_protocol_stdout(self):
        for mode in ('--bridge', '--worker'):
            with self.subTest(mode=mode):
                output, errors = io.StringIO(), io.StringIO()
                with redirect_stdout(output), redirect_stderr(errors), \
                        patch('chaoxing_desktop.launch_bootstrap.importlib.util.find_spec', return_value=None), \
                        patch('chaoxing_desktop.launch_bootstrap.project_python', return_value=Path('project-python.exe')), \
                        patch('chaoxing_desktop.launch_bootstrap.subprocess.call',
                              side_effect=lambda *a, **kw: print('{"type":"ready"}') or 0), \
                        patch('sys.argv', ['run.py', mode]):
                    self.assertEqual(relaunch_if_needed(), 0)
                self.assertEqual([json.loads(line) for line in output.getvalue().splitlines()], [{'type': 'ready'}])
                self.assertIn('project-python.exe', errors.getvalue())

    def test_relaunch_without_stderr(self):
        with patch('sys.stderr', None), \
                patch('chaoxing_desktop.launch_bootstrap.importlib.util.find_spec', return_value=None), \
                patch('chaoxing_desktop.launch_bootstrap.project_python', return_value=Path('project-python.exe')), \
                patch('chaoxing_desktop.launch_bootstrap.subprocess.call', return_value=0):
            self.assertEqual(relaunch_if_needed(), 0)

    def test_bootstrap_reports_missing_environment_without_installing_anything(self):
        with patch('chaoxing_desktop.launch_bootstrap.importlib.util.find_spec', return_value=None), \
                patch('chaoxing_desktop.launch_bootstrap.project_python', return_value=None):
            with self.assertRaisesRegex(RuntimeError, '虚拟环境'):
                relaunch_if_needed()

    def test_project_python_skips_broken_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'desktop'
            for env in (root / '.venv', root.parent / '.venv'):
                python = env / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
                python.parent.mkdir(parents=True)
                python.touch()
            with patch('chaoxing_desktop.launch_bootstrap.subprocess.run', side_effect=[Mock(returncode=1), Mock(returncode=0)]):
                self.assertEqual(project_python(root), root.parent / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python'))

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
