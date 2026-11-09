"""Release trimming must preserve the retained plugins' native dependencies."""
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from scripts.bundle_support import trim_bundle
from scripts.build_installer import retired_runtime_files


class BundlePolicyTests(unittest.TestCase):
    def test_upgrade_retirement_list_only_targets_runtime_files(self):
        files = retired_runtime_files()
        self.assertTrue(files)
        self.assertEqual(len(files), len(set(files)))
        self.assertTrue(all(path.parts[0] == '_internal' for path in files))

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


if __name__ == '__main__':
    unittest.main()
