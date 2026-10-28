import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCANNER = ROOT / "scripts" / "security_scan.ps1"
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell") or shutil.which("powershell.exe")
pytestmark = pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is unavailable")


def _scan(target: Path):
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCANNER), "-Root", str(target)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_security_scan_accepts_explicit_clean_root(tmp_path):
    (tmp_path / "source.txt").write_text("No credentials here.\n", encoding="utf-8")
    result = _scan(tmp_path)
    assert result.returncode == 0
    assert "Security scan passed" in result.stdout


def test_security_scan_redacts_detected_secret_value(tmp_path):
    key = "AKIA" + "1234567890123456"
    (tmp_path / "local.env").write_text("AWS_ACCESS_KEY_ID=" + key + "\n", encoding="utf-8")
    result = _scan(tmp_path)
    assert result.returncode == 1
    assert "AWS access key id" in result.stdout
    assert "potential secret value redacted" in result.stdout
    assert key not in result.stdout + result.stderr
