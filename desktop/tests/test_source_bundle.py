"""Keep user data out of public source archives."""
from pathlib import Path
import tempfile
import unittest
import zipfile

from scripts.source_bundle import source_files, repository_source_files, write_source_archive

# The native project needs these two public inputs to compile: the icon is both
# ApplicationIcon and a runtime Content item, and the project file is the build
# entry point.
NATIVE_INPUTS = ("desktop/assets/app.ico", "desktop/winui/ChaoxingDesktop.csproj")


class SourceBundleTests(unittest.TestCase):
    def test_repository_and_embedded_source_exclude_historical_material(self):
        included = {'README.md', 'LICENSE', '.gitignore',
                    '.github/workflows/windows-checks.yml', 'desktop/run.py',
                    'desktop/assets/app.ico', 'desktop/chaoxing_desktop/qml/Main.qml'}
        excluded = {'plans/history.md', 'plans/screenshot.png', 'docs/acceptance.md',
                    '.github/workflows/private.yml', 'release/old.exe', 'desktop/settings.ini',
                    'desktop/assets/private.ico', 'desktop/assets/icons/book.png'}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in included | excluded:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'fixture')
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
            "run.py", "README.md", "LICENSE", "WinUIBackend.spec",
            "requirements-lock.txt", "chaoxing_desktop/config.py",
            "chaoxing_desktop/qml/Main.qml", "scripts/build.ps1",
            "tests/test_settings.py", "assets/app.svg", "assets/app.ico",
            "assets/icons/book.svg",
            "vendor/chaoxing/api/base.py", "vendor/chaoxing/LICENSE",
            "vendor/chaoxing/resource/font_map_table.json",
        }
        excluded = {
            ".env", "settings.ini", "cookies.txt", "credentials.json",
            ".venv/site.py", ".venv-build/site.py", "build/temp.py",
            "ChaoxingDesktop.spec", "UPGRADE_ACCEPTANCE.md",
            "dist/app.py", ".ruff_cache/data.py", "chaoxing_desktop/settings.ini",
            "chaoxing_desktop/__pycache__/config.py", "scripts/token.key",
            "tests/live-response.json", "assets/private.pem",
            # The icon exemption is one named file, not a binary extension sweep.
            "assets/app.png", "assets/app.icns", "assets/private.ico",
            "winui/bin/MainWindow.xaml", "winui/obj/Generated.cs", "winui/publish/App.xaml",
            "winui/publish-motion/Generated.cs",
            "winui/dev/MainWindow.xaml", "winui/dev-lean/Generated.cs",
            "vendor/chaoxing/resource/credentials.json",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in included | excluded:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"fixture")
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
            "vendor/chaoxing/resource/ocr_charset.json", "vendor/chaoxing/resource/OCR-LICENSE.txt",
            "backend.py", "WinUIBackend.spec", "winui/MainWindow.xaml", "winui/ChaoxingDesktop.csproj",
            "assets/app.ico",
        }.issubset(names))
        self.assertFalse({"chaoxing_desktop/gui.py", "tests/gui_smoke.py"} & names)

    def test_archive_members_cover_native_compile_inputs(self):
        """The published ZIP itself, not just the selection helper, carries them."""
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "source.zip"
            write_source_archive(root.parent, archive)
            with zipfile.ZipFile(archive) as source:
                self.assertIsNone(source.testzip())
                members = set(source.namelist())
                prefix = "ChaoxingDesktop-source/"
                self.assertIn(prefix + NATIVE_INPUTS[0], members)
                self.assertIn(prefix + NATIVE_INPUTS[1], members)
                # Icon bytes survive the archive round trip, so a rebuild from
                # the extracted tree embeds the real icon.
                self.assertEqual(source.read(prefix + NATIVE_INPUTS[0]),
                                 (root.parent / NATIVE_INPUTS[0]).read_bytes())

    def test_extracted_archive_supplies_icon_and_project_inputs(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "source.zip"
            write_source_archive(root.parent, archive)
            with zipfile.ZipFile(archive) as source:
                source.extractall(directory)
            extracted = Path(directory) / "ChaoxingDesktop-source"
            icon = extracted / "desktop/assets/app.ico"
            project = extracted / "desktop/winui/ChaoxingDesktop.csproj"
            self.assertTrue(icon.is_file())
            self.assertTrue(project.is_file())
            # The project references the icon exactly as the extracted tree lays
            # it out, so a build from this tree resolves the same file.
            project_text = project.read_text(encoding="utf-8")
            self.assertIn("../assets/app.ico", project_text)
            self.assertEqual((project.parent / "../assets/app.ico").resolve(), icon.resolve())
            self.assertGreater(icon.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
