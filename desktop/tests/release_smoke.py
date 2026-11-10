"""Validate a packaged release, without relying on installed Python packages."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from scripts.check_smoke_report import verify_smoke_report

target = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else root / "dist/ChaoxingDesktop"
destination = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else root.parent / "plans/release-smoke"
destination.mkdir(parents=True, exist_ok=True)
env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QSG_RHI_BACKEND="software")
# No developer venv or Python directory in PATH.
env["PATH"] = os.path.join(env.get("WINDIR", "C:/Windows"), "System32")
with tempfile.TemporaryDirectory() as data:
    command = dict(action="probe", config={}, data_dir=data)
    probe = subprocess.run([str(target / "ChaoxingWorker.exe"), "--worker"], input=json.dumps(command),
                           capture_output=True, encoding="utf-8", timeout=30, env=env)
    assert probe.returncode == 0, probe.stdout + probe.stderr
    events = [json.loads(line) for line in probe.stdout.splitlines()]
    assert events[-1]["type"] == "probe", events
    assert events[-1].get('bank_protocol') == 'v2-text-and-images', events
    assert events[-1].get('captcha_ready') is True, events
    assert events[-1].get('review_policy_ready') is True, events
    assert events[-1].get('matching_ready'), events
    assert events[-1].get('ocs_ready') is True, events
    assert events[-1].get('discussion_ready') is True, events
    assert events[-1].get('activity_ready') is True, events
    print("Frozen core, font resources, v2 bank parameters, PNG Base64 codec, OCR and discussion modules passed.")
if "--worker-only" in sys.argv:
    raise SystemExit(0)
# Use the same native Windows launch route as normal desktop use. The GUI
# reports diagnostics through result.json, independently of console handles.
# A report from an earlier build is never reused for this run.
(destination / "result.json").unlink(missing_ok=True)
command = [str(target / "ChaoxingDesktop.exe"), "--smoke-test", destination.as_posix()]
if os.name == "nt":
    powershell = Path(env.get("WINDIR", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    command = [str(powershell), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
               str(root / "scripts/run_gui_smoke.ps1"), "-Executable", str(target / "ChaoxingDesktop.exe"),
               "-Destination", str(destination)]
result = subprocess.run(command, timeout=60, env=env)
assert result.returncode == 0, f"Windowed launcher exited with {result.returncode}; see {destination / 'result.json'}"
report = verify_smoke_report(destination / "result.json")
print(report["message"])
