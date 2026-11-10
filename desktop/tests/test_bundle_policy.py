"""Release trimming must preserve the retained plugins' native dependencies."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.bundle_support import trim_bundle
from scripts.build_installer import (installer_copy_ignore, retired_installer_lines,
                                     retired_runtime_files)


class BundlePolicyTests(unittest.TestCase):
    def test_installer_omits_symbols_but_keeps_runtime_and_license(self):
        with TemporaryDirectory() as directory:
            for name in ('App.pdb', 'App.dll', 'LICENSE'):
                (Path(directory) / name).touch()
            (Path(directory) / 'data.pdb').mkdir()
            self.assertEqual(installer_copy_ignore(directory,
                ['App.pdb', 'App.dll', 'LICENSE', 'data.pdb']), ['App.pdb'])

    def test_native_backend_omits_numpy_internal_test_extension(self):
        paths = ['numpy/_core/_multiarray_tests.cp312-win_amd64.pyd',
                 'numpy/_core/_multiarray_umath.cp312-win_amd64.pyd']
        with patch('scripts.bundle_support.pefile.PE', side_effect=self.pe({})):
            binaries, _ = trim_bundle([self.entry(p) for p in paths], [], native_backend=True)
        self.assertEqual([e[0] for e in binaries], paths[1:])

    def test_upgrade_retirement_list_only_targets_managed_files(self):
        files = retired_runtime_files()
        self.assertTrue(files)
        self.assertEqual(len(files), len(set(files)))
        self.assertEqual([path.as_posix() for path in files if path.parts[0] != '_internal'],
                         ['Source/ChaoxingDesktop-0.4.2-source.zip'])

    def test_retirement_lists_cover_the_archived_qml_payload(self):
        """v0.4.2 shipped Qt Quick; the upgrade must name those files exactly."""
        legacy = {path.as_posix() for path in retired_runtime_files()}
        names = (Path(__file__).resolve().parents[1]
                 / 'scripts' / 'retired-runtime-0.4.2.txt')
        qml = {line.strip() for line in names.read_text(encoding='utf-8').splitlines()
               if line.strip() and not line.startswith('#')}
        self.assertIn('_internal/PySide6/Qt6Qml.dll', qml)
        self.assertIn('_internal/PySide6/QtQuick.pyd', qml)
        self.assertIn('_internal/chaoxing_desktop/qml/Main.qml', qml)
        self.assertTrue(qml.issubset(legacy))
        # Only the old bundled source archive may sit outside the runtime tree.
        self.assertEqual({path for path in qml if not path.startswith('_internal/')},
                         {'Source/ChaoxingDesktop-0.4.2-source.zip'})
        self.assertFalse([path for path in qml if any(c in path for c in '*?')])
        self.assertFalse([path for path in qml if path.endswith(
            ('.ini', '.json', '.dat', '.log', '.key'))])

    def test_retired_installer_lines_delete_only_named_absent_files(self):
        with TemporaryDirectory() as directory:
            stage = Path(directory)
            (stage / '_internal/PySide6').mkdir(parents=True)
            (stage / '_internal/PySide6/Qt6Core.dll').write_bytes(b'kept')
            lines = retired_installer_lines(stage)
            self.assertIn('Delete "$INSTDIR\\_internal\\PySide6\\Qt6Qml.dll"', lines)
            self.assertIn('Delete "$INSTDIR\\Source\\ChaoxingDesktop-0.4.2-source.zip"', lines)
            # A file this payload still ships is never deleted.
            self.assertNotIn('Qt6Core.dll"', lines)
            # Directories that still exist are not pruned.
            self.assertNotIn('RMDir "$INSTDIR\\_internal\\PySide6"', lines)
            self.assertIn('RMDir "$INSTDIR\\_internal\\chaoxing_desktop\\qml"', lines)
            # Every obsolete ancestor is pruned, deepest first, so the old QML
            # tree leaves no empty folders behind.
            self.assertIn('RMDir "$INSTDIR\\_internal\\chaoxing_desktop"', lines)
            self.assertIn('RMDir "$INSTDIR\\_internal\\PySide6\\qml"', lines)
            rmdirs = [line for line in lines.splitlines() if line.startswith('RMDir ')]
            depths = [line.count(chr(92)) for line in rmdirs]
            self.assertEqual(depths, sorted(depths, reverse=True))
            # No wildcards and no recursive delete flag.
            self.assertNotIn('*', lines)
            self.assertNotIn(' /r', lines)

    def test_retirement_lists_match_their_evidence_fixture(self):
        root = Path(__file__).resolve().parents[1]
        evidence = json.loads((root / 'tests' / 'fixtures'
                               / 'retired-runtime-0.4.2-evidence.json').read_text(encoding='utf-8'))
        names = {line.strip() for line in
                 (root / 'scripts' / 'retired-runtime-0.4.2.txt').read_text(encoding='utf-8').splitlines()
                 if line.strip() and not line.startswith('#')}
        self.assertEqual(len(names), evidence['selectedEntries'])
        self.assertLessEqual(len(names), evidence['payloadEntries'])
        self.assertEqual(evidence['sha256'],
                         'f84097ab6dbd382f10616df63cf366d55305f5d67fe1970a5331a49b9336c61e')

    @staticmethod
    def entry(target):
        return target, target, 'BINARY'

    @staticmethod
    def pe(imports):
        def load(source, **kwargs):
            context = MagicMock()
            context.__enter__.return_value = SimpleNamespace(
                parse_data_directories=lambda **kwargs: None,
                DIRECTORY_ENTRY_IMPORT=[SimpleNamespace(dll=name.encode('ascii'))
                                        for name in imports.get(source, [])])
            return context
        return load

    def test_qml_plugin_closure_preserves_fallbacks_and_drops_unused_groups(self):
        paths = ['PySide6/QtQuick.pyd', 'PySide6/Qt6Quick.dll', 'PySide6/Qt6Core.dll',
                 'PySide6/opengl32sw.dll', 'PySide6/plugins/imageformats/qsvg.dll',
                 'PySide6/Qt6Svg.dll', 'PySide6/Qt6Quick3D.dll',
                 'PySide6/qml/QtQuick3D/quick3dplugin.dll',
                 'PySide6/qml/QtQuick/Controls/Fusion/fusionplugin.dll',
                 'cv2/opencv_videoio_ffmpeg500_64.dll']
        imports = {'PySide6/QtQuick.pyd': ['Qt6Quick.dll'],
                   'PySide6/Qt6Quick.dll': ['Qt6Core.dll'],
                   'PySide6/plugins/imageformats/qsvg.dll': ['Qt6Svg.dll']}
        data = [(path, path, 'DATA') for path in (
            'ddddocr/common_old.onnx', 'ddddocr/common.onnx', 'ddddocr/common_det.onnx',
            'PySide6/qml/QtQuick/Controls/FluentWinUI3/qmldir',
            'PySide6/qml/QtQuick/Controls/FluentWinUI3/Button.qml',
            'PySide6/qml/QtQuick/Controls/plugins.qmltypes',
            'chaoxing_desktop/qml/Main.qml')]
        with patch('scripts.bundle_support.pefile.PE', side_effect=self.pe(imports)):
            binaries, datas = trim_bundle([self.entry(path) for path in paths], data)
        targets = {entry[0] for entry in binaries}
        self.assertTrue(set(paths[:6]).issubset(targets))
        self.assertIn(paths[8], targets)
        self.assertNotIn(paths[6], targets)
        self.assertNotIn(paths[7], targets)
        self.assertNotIn(paths[9], targets)
        self.assertEqual({entry[0] for entry in datas}, {data[i][0] for i in (0, 3, 4, 6)})

    def test_required_excluded_dependency_aborts_build(self):
        paths = ['PySide6/QtQuick.pyd', 'PySide6/Qt6Pdf.dll']
        with patch('scripts.bundle_support.pefile.PE',
                   side_effect=self.pe({paths[0]: ['Qt6Pdf.dll']})):
            with self.assertRaisesRegex(RuntimeError, 'requires'):
                trim_bundle([self.entry(path) for path in paths], [])

    def test_native_backend_keeps_image_and_network_plugins_without_windowing(self):
        paths = ['PySide6/QtGui.pyd', 'PySide6/Qt6Gui.dll', 'PySide6/Qt6Core.dll',
                 'PySide6/plugins/imageformats/qsvg.dll', 'PySide6/Qt6Svg.dll',
                 'PySide6/plugins/tls/qschannelbackend.dll', 'PySide6/plugins/platforms/qwindows.dll',
                 'PySide6/plugins/styles/qmodernwindowsstyle.dll', 'PySide6/opengl32sw.dll']
        imports = {paths[0]: ['Qt6Gui.dll'], paths[1]: ['Qt6Core.dll'],
                   paths[3]: ['Qt6Svg.dll'], paths[4]: ['Qt6Gui.dll']}
        with patch('scripts.bundle_support.pefile.PE', side_effect=self.pe(imports)):
            binaries, datas = trim_bundle([self.entry(p) for p in paths],
                [('PySide6/translations/qt_zh_CN.qm', '', 'DATA')], native_backend=True)
        self.assertEqual({e[0] for e in binaries}, set(paths[:6]))
        self.assertFalse(datas)


if __name__ == '__main__':
    unittest.main()
