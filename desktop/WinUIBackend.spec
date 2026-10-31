# No QML engine or Qt Quick frontend in native releases.
from pathlib import Path
import sys
from importlib.metadata import distributions
from packaging.utils import canonicalize_name
from PyInstaller.utils.hooks import collect_submodules, collect_data_files, copy_metadata

root = Path(SPECPATH)
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / 'vendor/chaoxing'))
from chaoxing_desktop import __version__
from scripts.bundle_support import trim_bundle, version_resource

locked = {canonicalize_name(line.split('==')[0])
          for line in (root / 'requirements-lock.txt').read_text().splitlines()
          if line.strip() and not line.startswith('#')}
metadata = []
for dist in distributions():
    name = dist.metadata['Name']
    if canonicalize_name(name) in locked and canonicalize_name(name) not in {
        'pyinstaller', 'pyinstaller-hooks-contrib', 'pefile', 'pywin32-ctypes', 'altgraph',
        'pyside6-webengine', 'pyside6-pdf',
    }:
        metadata.extend(copy_metadata(name))

a = Analysis(
    [str(root / 'backend.py')], pathex=[str(root), str(root / 'vendor/chaoxing')],
    binaries=[],
    datas=[(str(root / 'vendor/chaoxing/resource'), 'vendor/chaoxing/resource')]
          + collect_data_files('ddddocr', includes=['common_old.onnx']) + metadata,
    hiddenimports=collect_submodules('api') + ['chaoxing_desktop.runtime', 'lxml.etree', 'pyaes'],
    excludes=['PySide6.QtQuick', 'PySide6.QtQml', 'PySide6.QtQuickControls2',
              'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineQuick',
              'PySide6.QtPdf', 'PySide6.QtPdfWidgets', 'tkinter', 'celery', 'flask'],
    noarchive=False,
)
a.binaries, a.datas = trim_bundle(a.binaries, a.datas)
pyz = PYZ(a.pure)
worker = EXE(pyz, a.scripts, [], exclude_binaries=True, name='ChaoxingWorker',
             console=True, strip=False, upx=False, icon=str(root / 'assets/app.ico'),
             version=version_resource(__version__, 'ChaoxingWorker'))
coll = COLLECT(worker, a.binaries, a.datas, strip=False, upx=False, name='ChaoxingBackend')
