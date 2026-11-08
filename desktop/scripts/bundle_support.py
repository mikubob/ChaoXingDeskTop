"""Windows bundle policy and executable version resources."""
from pathlib import Path
import re

import pefile
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable,
    VarFileInfo, VarStruct, VSVersionInfo,
)


def trim_bundle(binaries, datas, *, native_backend=False):
    # Remove whole optional plugin dependency groups, preserving SVG, raster
    # images, native Windows input, TLS and the software OpenGL fallback.
    unused = {
        "qpdf.dll", "qtvirtualkeyboardplugin.dll", "qt6pdf.dll",
        "qt6virtualkeyboard.dll", "qt6virtualkeyboardqml.dll",
        "qt6virtualkeyboardsettings.dll", "qt6pdfquick.dll",
    }

    def keep(entry):
        target = Path(entry[0])
        name = target.name.lower()
        parts = {part.lower() for part in target.parts}
        if native_backend and 'numpy' in parts and name.startswith('_multiarray_tests.'):
            return False  # NumPy's compiled internal test suite is not a runtime dependency.
        if native_backend and 'pyside6' in parts:
            if parts.intersection({'qml', 'qmltooling', 'translations'}):
                return False
            if 'plugins' in parts and not parts.intersection({'imageformats', 'tls', 'networkinformation'}):
                return False
            if name == 'opengl32sw.dll':
                return False  # Only CPU image codecs run here; WinUI owns rendering.
        if 'pyside6' in parts and 'qml' in parts:
            relative = tuple(part.lower() for part in target.parts)
            relative = relative[relative.index('qml') + 1:]
            if relative and relative[0] not in {'qtqml', 'qtquick'}:
                return False
            if (len(relative) > 2 and relative[0] == 'qtquick'
                    and relative[1] not in {'controls', 'templates', 'layouts', 'window', 'effects', 'nativestyle', 'shapes'}):
                return False
        if 'qmltooling' in parts or name.startswith('opencv_videoio_ffmpeg'):
            return False  # No QML debugger or OpenCV video capture in the app.
        if 'ddddocr' in parts and target.suffix == '.onnx' and name != 'common_old.onnx':
            return False  # DdddOcr(old=False, beta=False) uses common_old.onnx.
        return (not parts.intersection({"virtualkeyboard", "pdf", "qtwebengine"})
                and 'webengine' not in name and name not in unused
                and not re.fullmatch(r"icu(?:dt|in|uc)\d*\.dll|icu\.dll", name))

    imports = {}
    for target, source, _ in binaries:
        if Path(source).suffix.lower() not in {'.dll', '.pyd', '.exe'}:
            continue
        with pefile.PE(source, fast_load=True) as pe:
            pe.parse_data_directories(directories=[
                pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_IMPORT'],
                pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT'],
            ])
            imports[target] = {item.dll.decode('ascii').lower()
                               for attribute in ('DIRECTORY_ENTRY_IMPORT', 'DIRECTORY_ENTRY_DELAY_IMPORT')
                               for item in getattr(pe, attribute, [])}
    kept = [entry for entry in binaries if keep(entry)]
    # QML plugins load Qt DLLs dynamically. Start with retained plugins/Python
    # extensions, then retain their complete native dependency closure.
    qt_libraries = {Path(entry[0]).name.lower(): entry for entry in kept
                    if Path(entry[0]).name.lower().startswith('qt6')
                    and Path(entry[0]).suffix.lower() == '.dll'}
    pending = [entry for entry in kept if Path(entry[0]).name.lower() not in qt_libraries]
    required_qt = set()
    while pending:
        entry = pending.pop()
        for name in imports.get(entry[0], ()):
            if name in qt_libraries and name not in required_qt:
                required_qt.add(name)
                pending.append(qt_libraries[name])
    kept = [entry for entry in kept if Path(entry[0]).name.lower() not in qt_libraries
            or Path(entry[0]).name.lower() in required_qt]
    kept_targets = {entry[0] for entry in kept}
    removed = {Path(entry[0]).name.lower() for entry in binaries if entry[0] not in kept_targets}
    # Generic ICU imports are supplied by supported Windows versions. Numbered
    # host ICU and optional Qt libraries must not be required by retained files.
    removed -= {"icu.dll", "icuuc.dll", "icuin.dll"}
    for target, source, _ in kept:
        missing = imports.get(target, set()) & removed
        if missing:
            raise RuntimeError(f"Cannot trim bundle: {target} requires {sorted(missing)}")

    kept_data = [entry for entry in datas if keep(entry) and Path(entry[0]).suffix != '.qmltypes'
                 and not ("translations" in Path(entry[0]).parts
                         and Path(entry[0]).suffix == ".qm"
                         and not Path(entry[0]).stem.endswith("_zh_CN"))]
    print(f'Bundle policy: removed {len(binaries) - len(kept)} binaries and {len(datas) - len(kept_data)} data files')
    return kept, kept_data


def version_resource(version, executable):
    parts = tuple(int(part) for part in version.split("."))
    if len(parts) != 3:
        raise ValueError("Release version must have three numeric components")
    number = parts + (0,)
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=number, prodvers=number, mask=0x3f,
                          flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
        kids=[StringFileInfo([StringTable("080404B0", [
            StringStruct("FileDescription", "超星 Windows 桌面版" if executable == "ChaoxingDesktop" else "超星桌面版后台组件"),
            StringStruct("FileVersion", version),
            StringStruct("ProductName", "ChaoxingDesktop"),
            StringStruct("ProductVersion", version),
            StringStruct("InternalName", executable),
            StringStruct("OriginalFilename", executable + ".exe"),
            StringStruct("LegalCopyright", "GPL-3.0; see LICENSE and THIRD_PARTY_NOTICES.md"),
        ])]), VarFileInfo([VarStruct("Translation", [0x0804, 1200])])],
    )
