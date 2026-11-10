"""The Windows workflow must gate the native smoke on this run's frontend."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/windows-checks.yml"


class WindowsWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def step(self, name):
        """Return the body of the step whose name line is exactly name."""
        lines = self.text.splitlines()
        marker = "      - name: " + name
        start = next((index for index, line in enumerate(lines) if line.rstrip() == marker), None)
        self.assertIsNotNone(start, "workflow step missing: " + name)
        body = []
        for line in lines[start + 1:]:
            if line.startswith("      - "):
                break
            body.append(line)
        return "\n".join(body)

    def test_native_smoke_requires_this_runs_winui_report(self):
        body = self.step("Native WinUI 3 smoke from source")
        # The report file is removed by the launcher before the process starts,
        # so a leftover success cannot satisfy this check.
        self.assertIn("run.py --smoke-test build/ci-checks/source", body)
        self.assertIn("scripts/check_smoke_report.py build/ci-checks/source/result.json", body)
        self.assertIn("Source smoke did not report the WinUI 3 frontend", body)
        self.assertNotIn("--legacy-qml", body)

    def test_native_smoke_runs_once(self):
        self.assertEqual(self.text.count("--smoke-test"), 1)

    def test_qml_regression_stays_independent(self):
        body = self.step("Unit and protocol regressions (no credentials or external services)")
        self.assertIn("tests/qml_ui_smoke.py", body)
        self.assertIn("QML regression failed", body)

    def test_native_build_does_not_rerun_the_gui_smoke(self):
        body = self.step("Build native WinUI 3 frontend")
        self.assertIn("-SkipGuiSmoke", body)

    def test_frozen_worker_gate_is_unchanged(self):
        body = self.step("Build onedir and verify frozen worker")
        self.assertIn("github.event_name == 'workflow_dispatch'", body)
        self.assertIn("scripts/build.ps1", body)


if __name__ == "__main__":
    unittest.main()
