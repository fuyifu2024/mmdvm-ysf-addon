import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


class SafeInstallDryRunTests(unittest.TestCase):
    def test_dry_run_needs_no_sudo_and_changes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            etc = tmp / "etc"
            units = tmp / "units"
            etc.mkdir()
            units.mkdir()
            (etc / "addon.env").write_text(
                "ADDON=mmdvm-ysf-addon\nADDON_VERSION=0.3.0\n",
                encoding="utf-8")
            config = tmp / "config.env"
            text = (REPO / "config.example.env").read_text(encoding="utf-8")
            text = text.replace("CALLSIGN=N0CALL", "CALLSIGN=BI2RIP")
            text = text.replace("MODEM_PORT=auto", "MODEM_PORT=/dev/null")
            text = text.replace("ENABLE_OLED=auto", "ENABLE_OLED=0")
            config.write_text(text, encoding="utf-8")
            before = config.read_text(encoding="utf-8")
            env = os.environ.copy()
            env.update({
                "MMDVM_ETC_DIR": str(etc),
                "MMDVM_UNIT_DIR": str(units),
                "MMDVM_CONFIG": str(config),
            })
            result = subprocess.run(
                [str(REPO / "scripts" / "safe-install.sh"),
                 "--candidate", str(config), "--reason", "test",
                 "--dry-run", "--yes"],
                cwd=REPO, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("dry-run: no snapshot", result.stdout)
            self.assertEqual(config.read_text(encoding="utf-8"), before)


if __name__ == "__main__":
    unittest.main()
