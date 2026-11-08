"""Build a clean, per-user NSIS installer from an already verified application."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from chaoxing_desktop import __version__
from scripts.source_bundle import write_source_archive
from scripts.release_support import validate_app_tree
from scripts.provenance import verify_build


def retired_runtime_files():
    """Exact old payload names; never interpret user paths or wildcards."""
    paths = []
    for line in (ROOT / 'scripts/retired-runtime-0.3.5.txt').read_text(encoding='utf-8').splitlines():
        if not line or line.startswith('#'):
            continue
        path = Path(line)
        if (not line.startswith('_internal/') or path.is_absolute()
                or any(part in ('.', '..') for part in line.split('/'))
                or any(char in line for char in '*?:$"\\\r\n')):
            raise ValueError('Invalid retired runtime path')
        paths.append(path)
    return paths


def installer_copy_ignore(directory, names):
    """Keep debugging symbols outside the installed runtime payload."""
    return [name for name in names
            if Path(name).suffix.lower() == '.pdb' and (Path(directory) / name).is_file()]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--compiler", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT.parent / "release")
    args = parser.parse_args()
    app, compiler, output = args.app.resolve(), args.compiler.resolve(), args.output.resolve()
    if not compiler.is_file():
        raise FileNotFoundError(compiler)
    from PyInstaller.utils.win32.versioninfo import read_version_info_from_executable
    for name in ("ChaoxingDesktop.exe", "ChaoxingWorker.exe"):
        info = read_version_info_from_executable(str(app / name))
        expected = tuple(int(part) for part in __version__.split(".")) + (0,)
        if info is None or (info.ffi.fileVersionMS >> 16, info.ffi.fileVersionMS & 0xffff,
                            info.ffi.fileVersionLS >> 16, info.ffi.fileVersionLS & 0xffff) != expected:
            raise RuntimeError(f"{name} must be rebuilt for {__version__}")
    validate_app_tree(app)
    verify_build(ROOT, app)
    output.mkdir(parents=True, exist_ok=True)
    stage = ROOT / "build" / ("installer-stage-" + uuid.uuid4().hex[:12])
    shutil.copytree(app, stage, ignore=installer_copy_ignore)
    for name in ("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "requirements-lock.txt"):
        shutil.copy2(ROOT / name, stage / name)
    profile = f"installer-{__version__.replace('.', '_')}-{uuid.uuid4().hex[:16]}"
    (stage / "installation.json").write_text(json.dumps({"profile": profile}, indent=2), encoding="utf-8")
    source = stage / "Source" / f"ChaoxingDesktop-{__version__}-source.zip"
    source.parent.mkdir()
    write_source_archive(ROOT.parent, source)
    files = sorted(path for path in stage.rglob("*") if path.is_file())
    directories = sorted((path for path in stage.rglob("*") if path.is_dir()),
                         key=lambda path: len(path.parts), reverse=True)
    uninstall = stage.parent / (stage.name + "-uninstall.nsh")
    def nsis_path(path):
        return str(path.relative_to(stage)).replace("$", "$$").replace('"', '$\\"')
    uninstall.write_text("\n".join(
        [f'Delete "$INSTDIR\\{nsis_path(path)}"' for path in files]
        + [f'RMDir "$INSTDIR\\{nsis_path(path)}"' for path in directories]) + "\n", encoding="utf-8-sig")
    retired = stage.parent / (stage.name + '-retired.nsh')
    obsolete = [path for path in retired_runtime_files() if not (stage / path).exists()]
    retired.write_text('\n'.join(f'Delete "$INSTDIR\\{path}"' for path in obsolete)
                       + '\n', encoding='utf-8-sig')
    installer = output / f"ChaoxingDesktop-{__version__}-Setup-win-x64.exe"
    icon = ROOT / "assets/app.ico"
    if not icon.is_file():
        raise FileNotFoundError("assets/app.ico is missing; run scripts/make_app_icon.py first")
    subprocess.run([str(compiler), "/V2", "/INPUTCHARSET", "UTF8", f"/DAPP_VERSION={__version__}",
                    f"/DAPP_DIR={stage}", f"/DOUTPUT_EXE={installer}", f"/DAPP_ICON={icon}",
                    f"/DUNINSTALL_FILES={uninstall}", f"/DRETIRED_FILES={retired}",
                    str(ROOT / "scripts/installer.nsi")], check=True)
    shutil.copy2(source, output / source.name)
    manifest = dict(version=__version__, profile=profile,
                    files={path.name: dict(bytes=path.stat().st_size,
                                          sha256=hashlib.sha256(path.read_bytes()).hexdigest())
                           for path in (installer, output / source.name)})
    (output / f"ChaoxingDesktop-{__version__}-installer-SHA256.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")
    (ROOT / "build/installer-build.json").write_text(json.dumps(
        dict(stage=str(stage), installer=str(installer), profile=profile), indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
