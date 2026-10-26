import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import addon_config as ac  # noqa: E402


HOST_INI = """\
[General]
Callsign=BI2RIP
Id=1234567
[Modem]
UARTPort=/dev/ttyAMA0
UARTSpeed=115200
RXFrequency=144300000
[DMR]
Enable=1
Id=1234567
ColorCode=2
[D-Star]
Enable=1
Module=C
"""

YSF_INI = """\
[General]
Callsign=BI2RIP
Suffix=ND
[Info]
RXFrequency=144300000
Name=BI2RIP YSF Hotspot
Description=MMDVM YSF add-on
[Network]
Startup=CN-C4FM-TG46001
"""

HOSTS = """\
{
  "reflectors": [{
    "designator": "46001",
    "country": "CN",
    "name": "C4FM-TG46001",
    "description": "China C4FM TG46001",
    "port": 42000,
    "ipv4": "222.128.4.82"
  }]
}
"""

OLED_ENV = """\
OWN_CALL=BI2RIP
REFLECTOR_ID=46001
FREQUENCY_LABEL=144.300
I2C_BUS=1
I2C_ADDRESS=0x3C
OLED_CONTROLLER=sh1106
OLED_SIZE=128x64
OLED_ROTATE=180
OLED_CONTRAST=0xCF
"""

SYSTEM_ENV = """\
MODEM_PORT=/dev/ttyAMA0
ENABLE_OLED=1
ENABLE_DMR=1
ENABLE_DSTAR=1
DMR_ID=1234567
I2C_BUS=1
I2C_ADDRESS=0x3C
"""

DMR_INI = """\
[General]
Id=1234567
[Info]
ColorCode=2
[DMR Network 1]
Enabled=1
Address=4601.master.brandmeister.network
Port=62031
Password=secret-pw-123
"""

DSTAR_CFG = """\
[Gateway]
callsign=BI2RIP
[Repeater_1]
enabled=true
band=C
reflector=REF001 C
"""


class ConfigTests(unittest.TestCase):
    def test_parse_env_first_wins_and_quotes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.env"
            path.write_text(
                "# comment\nA=one\nB='two words'\nA=late\n", encoding="utf-8")
            values = ac.parse_env(path)
            self.assertEqual(values["A"], "one")
            self.assertEqual(values["B"], "two words")

    def test_merge_preserves_values_comments_and_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            example = tmp / "example.env"
            config = tmp / "config.env"
            example.write_text(
                "# example\nCALLSIGN=N0CALL\nFREQUENCY=438.8000\nNEW_OPTION=default\n",
                encoding="utf-8")
            original = "# user\nCALLSIGN=BI2RIP\nFREQUENCY=144.3000\n"
            config.write_text(original, encoding="utf-8")
            os.chmod(config, 0o600)
            added = ac.merge_missing_defaults(config, example)
            self.assertEqual(added, ["NEW_OPTION"])
            text = config.read_text(encoding="utf-8")
            self.assertTrue(text.startswith(original))
            self.assertIn("NEW_OPTION=default", text)
            self.assertEqual(text.count("CALLSIGN="), 1)
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)

    def test_update_env_values_updates_and_appends(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.env"
            path.write_text("A=1\n# keep\nB=2\n", encoding="utf-8")
            changed = ac.update_env_values(path, {"A": "9", "C": "3"})
            self.assertEqual(sorted(changed), ["A", "C"])
            text = path.read_text(encoding="utf-8")
            self.assertIn("A=9\n", text)
            self.assertIn("# keep\n", text)
            self.assertIn("C=3\n", text)

    def test_import_existing_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            etc = Path(tmp) / "etc"
            etc.mkdir()
            (etc / "MMDVM-Host.ini").write_text(HOST_INI, encoding="utf-8")
            (etc / "YSFGateway.ini").write_text(YSF_INI, encoding="utf-8")
            (etc / "YSFHosts.json").write_text(HOSTS, encoding="utf-8")
            (etc / "oled.env").write_text(OLED_ENV, encoding="utf-8")
            (etc / "system.env").write_text(SYSTEM_ENV, encoding="utf-8")
            (etc / "DMRGateway.ini").write_text(DMR_INI, encoding="utf-8")
            (etc / "DStarGateway.cfg").write_text(DSTAR_CFG, encoding="utf-8")
            result = ac.import_existing_install(etc)
            values = result.values
            self.assertEqual(values["CALLSIGN"], "BI2RIP")
            self.assertEqual(values["FREQUENCY"], "144.3000")
            self.assertEqual(values["MODEM_PORT"], "/dev/ttyAMA0")
            self.assertEqual(values["REFLECTOR_ID"], "46001")
            self.assertEqual(values["REFLECTOR_STARTUP"], "CN-C4FM-TG46001")
            self.assertEqual(values["REFLECTOR_HOST"], "222.128.4.82")
            self.assertEqual(values["OLED_CONTROLLER"], "sh1106")
            self.assertEqual(values["OLED_ROTATE"], "180")
            self.assertEqual(values["ENABLE_DMR"], "1")
            self.assertEqual(values["DMR_ID"], "1234567")
            self.assertEqual(values["DMR_PASSWORD"], "secret-pw-123")
            self.assertEqual(values["ENABLE_DSTAR"], "1")
            self.assertEqual(values["DSTAR_REFLECTOR"], "REF001 C")
            self.assertEqual(values["DSTAR_MODULE"], "C")
            self.assertNotIn("secret", ac.mask_secret("DMR_PASSWORD", "secret-pw-123"))

    def test_diff_masks_secret(self):
        rows = ac.candidate_diff({"DMR_PASSWORD": "oldsecret"},
                                 {"DMR_PASSWORD": "newsecret"})
        self.assertEqual(rows[0][0], "DMR_PASSWORD")
        self.assertEqual(rows[0][1], "<hidden>")
        self.assertEqual(rows[0][2], "<hidden>")

    def test_install_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            etc = tmp / "etc"
            units = tmp / "units"
            self.assertEqual(ac.install_state(etc, units), "none")
            etc.mkdir()
            units.mkdir()
            self.assertEqual(ac.install_state(etc, units), "foreign")
            (etc / "MMDVM-Host.ini").write_text("[General]\n", encoding="utf-8")
            self.assertEqual(ac.install_state(etc, units), "foreign")
            (units / "ysfgateway.service").write_text("[Unit]\n", encoding="utf-8")
            self.assertEqual(ac.install_state(etc, units), "legacy")
            (etc / "addon.env").write_text("ADDON=mmdvm-ysf-addon\n",
                                           encoding="utf-8")
            self.assertEqual(ac.install_state(etc, units), "addon")


class UpdateCliTests(unittest.TestCase):
    def test_update_dry_run_does_not_write(self):
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
            config.write_text(
                (REPO / "config.example.env").read_text(encoding="utf-8"),
                encoding="utf-8")
            before = config.read_text(encoding="utf-8")
            env = os.environ.copy()
            env.update({
                "MMDVM_ETC_DIR": str(etc),
                "MMDVM_UNIT_DIR": str(units),
            })
            result = subprocess.run(
                [sys.executable, str(REPO / "scripts" / "update.py"),
                 "--dry-run", "--no-pull", "--config", str(config)],
                cwd=REPO, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("install state:      addon", result.stdout)
            self.assertIn("no files changed", result.stdout)
            self.assertEqual(config.read_text(encoding="utf-8"), before)


if __name__ == "__main__":
    unittest.main()

