# Archived Python GUI specification; native builds use WinUIBackend.spec.
from pathlib import Path
import sys
from importlib.metadata import distributions
from packaging.utils import canonicalize_name
from PyInstaller.utils.hooks import collect_submodules, collect_data_files, copy_metadata

root = Path(SPECPATH)
sys.path.insert(0, str(root))
from chaoxing_desktop import __version__
from scripts.bundle_support import trim_bundle, version_resource
sys.path.insert(0, str(root / 'vendor/chaoxing'))
hidden = collect_submodules('api') + ['chaoxing_desktop.runtime', 'lxml.etree', 'lxml._elementpath', 'pyaes']
metadata = []
locked_names = {canonicalize_name(line.split('==')[0]) for line in (root / 'requirements-lock.txt').read_text().splitlines() if line.strip() and not line.startswith('#')}
for dist in distributions():
    name = dist.metadata['Name']
    if canonicalize_name(name) in locked_names and canonicalize_name(name) not in ('pyinstaller', 'pyinstaller-hooks-contrib', 'pyside6-webengine', 'pyside6-pdf', 'pefile', 'pywin32-ctypes', 'altgraph', 'pip'):
        metadata.extend(copy_metadata(name))
a = Analysis(
    [str(root / 'run.py')],
    pathex=[str(root), str(root / 'vendor/chaoxing')],
    binaries=[],
    datas=[(str(root / 'vendor/chaoxing/resource'), 'vendor/chaoxing/resource')]
          + [(str(root / 'assets'), 'assets'), (str(root / 'chaoxing_desktop/qml'), 'chaoxing_desktop/qml'), (str(root / 'LICENSE'), '.'),
           (str(root / 'THIRD_PARTY_NOTICES.md'), '.')] + metadata + collect_data_files('ddddocr', includes=['common_old.onnx']),
    hiddenimports=hidden,
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['cv2', 'ddddocr', 'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineQuick',
              'PySide6.QtPdf', 'PySide6.QtPdfWidgets', 'PySide6.QtVirtualKeyboard',
              'tkinter', 'celery', 'flask', 'openai', 'httpx', 'httpx2', 'tqdm'],
    noarchive=False,
)
# Qt uses the ICU API supplied by supported Windows releases. The build host
# may have unrelated Poppler/Conda ICU DLLs in PATH with incompatible exports.
# Never shadow the Windows API with those generic DLL names.
a.binaries, a.datas = trim_bundle(a.binaries, a.datas)
pyz = PYZ(a.pure)
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name='ChaoxingDesktop', debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=False,
          icon=str(root / 'assets/app.ico'),
          version=version_resource(__version__, 'ChaoxingDesktop'))
worker = EXE(pyz, a.scripts, [], exclude_binaries=True, name='ChaoxingWorker', debug=False,
             bootloader_ignore_signals=False, strip=False, upx=False, console=True,
             icon=str(root / 'assets/app.ico'),
             version=version_resource(__version__, 'ChaoxingWorker'))
coll = COLLECT(gui, worker, a.binaries, a.datas, strip=False, upx=False, name='ChaoxingDesktop')
