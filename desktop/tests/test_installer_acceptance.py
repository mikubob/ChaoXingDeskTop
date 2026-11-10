"""Static checks on the isolated install/upgrade acceptance script."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / 'tests/installer_smoke.ps1'
EVIDENCE = ROOT / 'tests/fixtures/retired-runtime-0.4.2-evidence.json'


class InstallerSmokePolicyTest(unittest.TestCase):
    def setUp(self):
        self.text = SMOKE.read_text(encoding='utf-8')

    def test_previous_installer_stays_a_generic_overlay_upgrade(self):
        # Any previous installer exercises the generic upgrade path; the retired
        # payload assertion must not reject a WinUI to WinUI upgrade.
        self.assertIn('[string]$PreviousInstaller', self.text)
        self.assertIn('[switch]$ExpectRetiredPayload', self.text)

    def test_retired_payload_check_needs_the_reviewed_qml_identity(self):
        self.assertIn('retired-runtime-0.4.2-evidence.json', self.text)
        self.assertIn('Get-FileHash', self.text)
        self.assertIn('$taskEvidence.profile', self.text)
        # Vacuity is measured against the QML list only: a WinUI build may still
        # contain an entry of the older 0.3.5 list.
        self.assertIn('$taskQmlObsoleteBefore', self.text)
        self.assertIn('if ($taskQmlPayload -and $taskQmlObsoleteBefore -eq 0)', self.text)
        self.assertNotIn('if ($taskPreviousProfile -and $taskObsoleteBefore -eq 0)', self.text)

    def test_report_names_the_retired_payload_outcome(self):
        self.assertIn('retiredPayloadTested=$taskQmlPayload', self.text)
        self.assertIn('retiredFilesPresentBefore=$taskObsoleteBefore', self.text)
        self.assertIn('upgradeLeftovers=$taskLeftovers.Count', self.text)
        self.assertIn('uninstallRemainingFiles=$taskRemaining.Count', self.text)

    def test_upgrade_and_uninstall_compare_the_whole_previous_tree(self):
        # A mismatch in one retired list entry is not enough: the upgrade must
        # drop every file the previous installer wrote and this payload does not
        # provide, and the uninstall must leave no application payload at all.
        self.assertIn('$taskOldTree = @(Get-ChildItem -LiteralPath $taskInstalled -File -Recurse', self.text)
        self.assertIn('if ($taskStageRelative.ContainsKey($taskRelative)', self.text)
        self.assertIn("throw ('Upgrade retained previous application files: '", self.text)
        self.assertIn("throw ('Uninstall retained application payload: '", self.text)
        # The user's own files and the installer's own bookkeeping are exempt,
        # and both stay explicitly asserted as preserved.
        self.assertIn("@('keep-upgrade-file.txt', 'keep-user-file.txt')", self.text)
        self.assertIn("throw 'Uninstall removed extra user file'", self.text)
        # Still no wildcard and no recursive delete of the installed tree.
        self.assertNotIn('Remove-Item', self.text)

    def test_whole_tree_failure_is_scoped_to_the_reviewed_qml_payload(self):
        # The hard "no previous application file survives" rule is specific to
        # the reviewed QML migration; a generic WinUI overlay only proves that
        # the new payload is installed and user data is kept.
        self.assertIn('if ($taskQmlPayload -and $taskLeftovers.Count -gt 0)', self.text)
        self.assertIn('upgradeLeftoversEnforced=$taskQmlPayload', self.text)
        # Files this payload installed are still removed by any uninstall.
        self.assertIn('$taskManagedLeft', self.text)
        self.assertIn("throw ('Uninstall retained installed payload: '", self.text)

    def test_user_sentinel_survives_a_retired_directory(self):
        # A user file seeded inside a retired directory (_internal/cv2) is
        # checked by hash after the upgrade and after the uninstall, and it is
        # the only extra path allowed to remain for the reviewed QML payload.
        self.assertIn("$taskSentinel = if ($taskQmlPayload) { '_internal/cv2/synthetic-user-keep.txt' }", self.text)
        self.assertIn('Assert-SentinelPreserved', self.text)
        self.assertIn("Assert-SentinelPreserved 'the upgrade'", self.text)
        self.assertIn("Assert-SentinelPreserved 'the uninstall'", self.text)
        self.assertIn('sentinelPreserved=', self.text)
        # Comparisons against the installed tree use the Windows separator.
        self.assertIn("$taskSentinelRelative = $taskSentinel.Replace('/', '\\')", self.text)
        self.assertIn('@($taskSentinelRelative', self.text)
        seed = self.text.index('Cannot seed the user sentinel')
        self.assertLess(self.text.index('$taskOldTree = @(Get-ChildItem'), seed)

    def test_evidence_fixture_identifies_the_installer(self):
        import json
        evidence = json.loads(EVIDENCE.read_text(encoding='utf-8'))
        self.assertRegex(evidence['sha256'], r'^[a-f0-9]{64}$')
        self.assertRegex(evidence['profile'], r'^installer-0_4_2-[a-f0-9]{16}$')
        self.assertTrue(evidence['verifiedAbsentFromWinUiPayload'])

    def test_readme_documents_the_retired_payload_switch(self):
        readme = (ROOT / 'README.md').read_text(encoding='utf-8')
        self.assertIn('-ExpectRetiredPayload', readme)
        self.assertIn('retired-runtime-0.4.2.txt', readme)


if __name__ == '__main__':
    unittest.main()