"""Keep user data out of public source archives."""
from pathlib import Path
import tempfile
import unittest
import zipfile

from scripts.source_bundle import source_files, repository_source_files, write_source_archive


class SourceBundleTests(unittest.TestCase):
    def test_repository_and_embedded_source_exclude_historical_material(self):
        included = {'README.md', 'LICENSE', '.gitignore',
                    '.github/workflows/windows-checks.yml', 'desktop/run.py',
                    'desktop/chaoxing_desktop/qml/Main.qml'}
        excluded = {'plans/history.md', 'plans/screenshot.png', 'docs/acceptance.md',
                    '.github/workflows/private.yml', 'release/old.exe', 'desktop/settings.ini'}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in included | excluded:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('fixture', encoding='utf-8')
            actual = {path.relative_to(root).as_posix() for path in repository_source_files(root)}
            self.assertEqual(actual, included)
            archive = root / 'source.zip'
            write_source_archive(root, archive)
            with zipfile.ZipFile(archive) as source:
                self.assertIsNone(source.testzip())
                self.assertEqual(set(source.namelist()),
                                 {'ChaoxingDesktop-source/' + name for name in included})

    def test_public_resources_included_and_local_files_excluded(self):
        included = {
            "run.py", "README.md", "LICENSE", "ChaoxingDesktop.spec",
            "requirements-lock.txt", "chaoxing_desktop/config.py",
            "chaoxing_desktop/qml/Main.qml", "scripts/build.ps1",
            "tests/test_settings.py", "assets/app.svg", "assets/icons/book.svg",
            "vendor/chaoxing/api/base.py", "vendor/chaoxing/LICENSE",
            "vendor/chaoxing/resource/font_map_table.json",
        }
        excluded = {
            ".env", "settings.ini", "cookies.txt", "credentials.json",
            ".venv/site.py", ".venv-build/site.py", "build/temp.py",
            "dist/app.py", ".ruff_cache/data.py", "chaoxing_desktop/settings.ini",
            "chaoxing_desktop/__pycache__/config.py", "scripts/token.key",
            "tests/live-response.json", "assets/private.pem",
            "winui/bin/MainWindow.xaml", "winui/obj/Generated.cs", "winui/publish/App.xaml",
            "winui/publish-motion/Generated.cs",
            "vendor/chaoxing/resource/credentials.json",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in included | excluded:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture", encoding="utf-8")
            actual = {p.relative_to(root).as_posix() for p in source_files(root)}
        self.assertEqual(actual, included)

    def test_real_source_contains_current_entry_and_required_resources(self):
        root = Path(__file__).resolve().parents[1]
        names = {p.relative_to(root).as_posix() for p in source_files(root)}
        self.assertTrue({
            "chaoxing_desktop/qml_app.py", "chaoxing_desktop/qml_backend.py",
            "chaoxing_desktop/qml/Main.qml", "chaoxing_desktop/qml_selftest.py",
            "chaoxing_desktop/worker.py", "vendor/chaoxing/api/captcha.py",
            "vendor/chaoxing/resource/font_map_table.json", "LICENSE",
            "backend.py", "WinUIBackend.spec", "winui/MainWindow.xaml", "winui/ChaoxingDesktop.csproj",
        }.issubset(names))
        self.assertFalse({"chaoxing_desktop/gui.py", "tests/gui_smoke.py"} & names)


if __name__ == "__main__":
    unittest.main()
