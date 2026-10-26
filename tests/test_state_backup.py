import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


class StateBackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.etc = root / "etc"
        self.bin = root / "bin"
        self.units = root / "units"
        self.dropin = self.units / "mmdvmhost.service.d"
        self.backup = root / "backups"
        self.config = root / "config.env"
        self.logrotate = root / "logrotate" / "mmdvm"
        for path in (self.etc, self.bin, self.units, self.dropin,
                     self.logrotate.parent):
            path.mkdir(parents=True, exist_ok=True)
        self.etc_mode = self.etc.stat().st_mode & 0o777
        (self.etc / "MMDVM-Host.ini").write_text("old-host\n", encoding="utf-8")
        os.chmod(self.etc / "MMDVM-Host.ini", 0o640)
        (self.bin / "YSFGateway").write_text("old-bin\n", encoding="utf-8")
        (self.units / "ysfgateway.service").write_text("old-unit\n", encoding="utf-8")
        (self.dropin / "20-port-group.conf").write_text("old-dropin\n",
                                                        encoding="utf-8")
        self.config.write_text("CALLSIGN=BI2RIP\n", encoding="utf-8")
        os.chmod(self.config, 0o600)
        self.logrotate.write_text("old-logrotate\n", encoding="utf-8")
        self.env = os.environ.copy()
        self.env.update({
            "MMDVM_TEST_MODE": "1",
            "MMDVM_ETC_DIR": str(self.etc),
            "MMDVM_BIN_DIR": str(self.bin),
            "MMDVM_UNIT_DIR": str(self.units),
            "MMDVM_DROPIN_DIR": str(self.dropin),
            "MMDVM_BACKUP_ROOT": str(self.backup),
            "MMDVM_CONFIG": str(self.config),
            "MMDVM_LOGROTATE": str(self.logrotate),
        })

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(REPO / "scripts" / "state_backup.py"), *args],
            cwd=REPO, env=self.env, capture_output=True, text=True)

    def test_create_and_restore(self):
        result = self._run("create")
        self.assertEqual(result.returncode, 0, result.stderr)
        target = Path(result.stdout.strip().splitlines()[-1])
        self.assertTrue((target / "etc" / "MMDVM-Host.ini").is_file())
        self.assertTrue((target / "bin" / "YSFGateway").is_file())
        self.assertTrue((target / "config" / "config.env").is_file())
        manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(manifest["etc_present"])
        self.assertTrue(manifest["config_present"])

        (self.etc / "MMDVM-Host.ini").write_text("new-host\n", encoding="utf-8")
        (self.bin / "YSFGateway").write_text("new-bin\n", encoding="utf-8")
        (self.units / "ysfgateway.service").write_text("new-unit\n", encoding="utf-8")
        (self.dropin / "20-port-group.conf").write_text("new-dropin\n",
                                                        encoding="utf-8")
        self.config.write_text("CALLSIGN=N0CALL\n", encoding="utf-8")
        self.logrotate.write_text("new-logrotate\n", encoding="utf-8")

        result = self._run("restore", str(target))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.etc / "MMDVM-Host.ini").read_text(encoding="utf-8"),
                         "old-host\n")
        self.assertEqual((self.bin / "YSFGateway").read_text(encoding="utf-8"),
                         "old-bin\n")
        self.assertEqual((self.units / "ysfgateway.service").read_text(encoding="utf-8"),
                         "old-unit\n")
        self.assertEqual((self.dropin / "20-port-group.conf").read_text(encoding="utf-8"),
                         "old-dropin\n")
        self.assertEqual(self.config.read_text(encoding="utf-8"), "CALLSIGN=BI2RIP\n")
        self.assertEqual(self.logrotate.read_text(encoding="utf-8"),
                         "old-logrotate\n")
        self.assertEqual(self.etc.stat().st_mode & 0o777, self.etc_mode)

    def test_prune_keeps_newest(self):
        for _ in range(6):
            result = self._run("create", "--keep", "3")
            self.assertEqual(result.returncode, 0, result.stderr)
        backups = sorted(p for p in self.backup.iterdir() if p.is_dir())
        self.assertLessEqual(len(backups), 3)


if __name__ == "__main__":
    unittest.main()
