"""Select public source files without collecting local data or build output."""
from pathlib import Path
import zipfile


def source_files(desktop: Path):
    root_files = (
        ".gitignore", "run.py", "backend.py", "WinUIBackend.spec", "README.md", "LICENSE",
        "THIRD_PARTY_NOTICES.md", "requirements.txt", "requirements-build.txt",
        "requirements-lock.txt",
        "scripts/retired-runtime-0.3.5.txt",
        "scripts/retired-runtime-0.4.2.txt",
        "scripts/retired-runtime-0.6.5.txt",
        "tests/test_ai_resilience.py",
        "tests/test_decode_choices.py",
        "tests/test_exam_config.py",
        "tests/test_exam_drafts.py",
        "tests/test_exam_protocol.py",
        "tests/test_formula_images.py",
        "tests/test_homework_drafts.py",
        "tests/test_ordered_chapters.py",
        "tests/test_winui_bridge.py",
        # SVG 是应用图标源文件，ICO 供 WinUI 和冻结后台构建使用。
        "assets/app.svg", "assets/app.ico",
    )
    files = [desktop / name for name in root_files]
    for path in (desktop / 'winui').rglob('*'):
        if (path.is_file() and not path.is_symlink()
                and not {'bin', 'obj', 'publish'}.intersection(path.relative_to(desktop / 'winui').parts)
                and not any(part.startswith(('publish-', 'dev-')) or part == 'dev' for part in path.relative_to(desktop / 'winui').parts)
                and path.suffix in {'.cs', '.xaml', '.csproj', '.manifest', '.md'}):
            files.append(path)
    for directory, extensions in (
        ("chaoxing_desktop", {".py"}),
        ("scripts", {".py", ".ps1", ".nsi"}),
        ("tests/fixtures/exam", {".html"}),
        ("tests/fixtures/homework", {".html"}),
        ("vendor/chaoxing/api", {".py", ".md"}),
    ):
        for path in (desktop / directory).rglob("*"):
            if (path.is_file() and not path.is_symlink()
                    and "__pycache__" not in path.parts and path.suffix in extensions):
                files.append(path)
    files.extend(desktop / relative for relative in (
        "vendor/chaoxing/LICENSE", "vendor/chaoxing/resource/README.md",
        "vendor/chaoxing/resource/font_map_table.json",
        "vendor/chaoxing/resource/ocr_charset.json", "vendor/chaoxing/resource/OCR-LICENSE.txt",
    ))
    return sorted(path for path in files if path.is_file() and not path.is_symlink())


def repository_source_files(root: Path):
    """One explicit public scope for Git publishing and embedded GPL source."""
    files = source_files(root / 'desktop')
    files.extend(root / name for name in ('.gitignore', 'README.md', 'LICENSE', 'AGENTS.md',
                                         '.github/workflows/windows-checks.yml'))
    return sorted(path for path in files if path.is_file() and not path.is_symlink())


def write_source_archive(root: Path, destination: Path):
    with zipfile.ZipFile(destination, 'w') as archive:
        for path in repository_source_files(root):
            # 固定 ZIP 条目元数据，避免沿用本机文件时间和权限。
            entry = zipfile.ZipInfo('ChaoxingDesktop-source/' + path.relative_to(root).as_posix())
            entry.create_system = 0
            archive.writestr(entry, path.read_bytes(), zipfile.ZIP_DEFLATED, compresslevel=6)
