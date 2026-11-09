"""Select public source files without collecting local data or build output."""
from pathlib import Path
import zipfile


def source_files(desktop: Path):
    root_files = (
        ".gitignore", "run.py", "ChaoxingDesktop.spec", "README.md", "LICENSE",
        "THIRD_PARTY_NOTICES.md", "UPGRADE_ACCEPTANCE.md", "REVIEWED_SUBMISSION_CONTRACT.md", "requirements.txt", "requirements-build.txt",
        "requirements-lock.txt",
        "scripts/retired-runtime-0.3.5.txt",
    )
    files = [desktop / name for name in root_files]
    for directory, extensions in (
        ("chaoxing_desktop", {".py", ".qml"}),
        ("scripts", {".py", ".ps1", ".nsi"}),
        ("tests", {".py", ".ps1"}),
        ("assets", {".svg"}),
        ("vendor/chaoxing/api", {".py", ".md"}),
    ):
        for path in (desktop / directory).rglob("*"):
            if (path.is_file() and not path.is_symlink()
                    and "__pycache__" not in path.parts and path.suffix in extensions):
                files.append(path)
    files.append(desktop / "tests/fixtures/ocs_protocol_cases.json")
    files.append(desktop / "tests/fixtures/verification_cases.json")
    files.append(desktop / "tests/fixtures/README.md")
    files.extend(desktop / relative for relative in (
        "vendor/chaoxing/LICENSE", "vendor/chaoxing/resource/README.md",
        "vendor/chaoxing/resource/font_map_table.json",
    ))
    return sorted(path for path in files if path.is_file() and not path.is_symlink())


def repository_source_files(root: Path):
    """One explicit public scope for Git publishing and embedded GPL source."""
    files = source_files(root / 'desktop')
    files.extend(root / name for name in ('.gitignore', 'README.md', 'LICENSE',
                                         '.github/workflows/windows-checks.yml'))
    return sorted(path for path in files if path.is_file() and not path.is_symlink())


def write_source_archive(root: Path, destination: Path):
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in repository_source_files(root):
            archive.write(path, 'ChaoxingDesktop-source/' + path.relative_to(root).as_posix())
