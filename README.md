# mmdvm-ysf-addon

**Add an MMDVM YSF hotspot to the Linux system you already use — no dedicated image, no OS replacement, reversible.**

English | [简体中文](README.zh-CN.md)

![OLED Live Caller](docs/oled-live-caller.png)

---

## What this is

Most MMDVM hotspot guides ask you to flash a dedicated image (Pi-Star / WPSD) and turn
the whole machine into a hotspot. This project is the other approach: **an add-on for
a machine you already use** — a home server, a desktop Pi, or a radio PC running
Ubuntu/Debian.

It adds:

- a dedicated `mmdvm` system user
- three systemd services: `ysfgateway`, `mmdvmhost`, `oled-display`
- `/etc/mmdvm`, `/var/log/mmdvm`, `/var/lib/mmdvm`
- `/usr/local/bin/{MMDVM-Host,YSFGateway,oled_display.py}`
- a boot overlay on Raspberry Pi (`uart0-pi5` on Pi 5; `enable_uart` + BT overlay on
  Pi 4/3 — experimental)
- build/runtime dependencies listed below

It does **not** replace the OS, change boot order, flash HAT firmware, reinstall the
desktop, or take over unrelated services. A platform without a Pi GPIO header (x86)
gets no boot-config changes at all.

> Honest note: this is **not** a zero-change install. It adds packages, a user,
> directories, three services and (on Pi) boot-config lines. That is why the project
> ships preflight conflict checks and an uninstall path. The “What it changes” table
> below lists everything.

## Platform support

| Platform | Status | Modem connection | OLED |
|---|---|---|---|
| **Raspberry Pi 5 + Ubuntu 26.04** | **Tested by the author** | GPIO14/15 HAT, `dtoverlay=uart0-pi5` | Pi I2C-1, 0x3C (tested) |
| Raspberry Pi 4 / 3 / Zero 2 W | **Experimental — not hardware-tested** | GPIO14/15 HAT, `enable_uart=1` + `miniuart-bt`/`disable-bt`, reboot | Pi I2C, theoretically |
| x86_64 Ubuntu / Debian | **Build/CI only — hardware NOT tested** | **USB serial MMDVM modem** (`ttyUSB`/`ttyACM`); a Pi GPIO HAT cannot plug in directly | Usually none; optional external I2C is untested |
| Other Linux / special UART bridges | Manual / unsupported | USB serial; set `MODEM_PORT` yourself | Optional/manual |

CI only proves that the upstream sources, patches and this project's build run on an
x86_64 GitHub runner. It does **not** prove UART, USB enumeration, I2C, systemd
installation, RF, or a reflector link on any of those platforms. Only the Pi 5
configuration below was tested with real hardware.

## Tested configuration

- Raspberry Pi 5 Model B, **Ubuntu 26.04.1 LTS (arm64)**
- MMDVM_HS_Hat firmware `MMDVM_HS_Hat-v1.5.2 20201108 14.7456MHz ADF7021 FW by CA6JAU`
- On-board SSD1306 OLED on Pi I2C-1 at `0x3C`
- YSF reflector `46001 / CN-C4FM-TG46001` (`222.128.4.82:42000`)
- Author: BI2RIP (FT-5D / FTM-510D)

The author also field-tested a **144.3000 MHz 2m** setup on this HAT. The public
example intentionally uses **438.8000 MHz** instead. 144.300 is the 2m weak-signal/SSB
calling area and is **not** recommended as a hotspot default; see the frequency note.

## Requirements

- An existing Linux machine; Ubuntu/Debian + systemd is the supported install path.
- For Pi: a compatible MMDVM HAT with a working STM32 firmware, and free GPIO14/15.
- For x86: a **USB serial MMDVM modem** (or a USB-TTL adapter wired to a modem).
- A valid amateur call sign and a locally coordinated frequency.
- Python 3 + Pillow (`python3-pil`) for the optional OLED.
- No existing MMDVM / Pi-Star / WPSD install on the same machine; preflight refuses
  to overwrite one.
- This project does **not** flash firmware or provide a prebuilt OS image.

## Quick start

```bash
git clone https://github.com/fuyifu2024/mmdvm-ysf-addon.git
cd mmdvm-ysf-addon

cp config.example.env config.env
$EDITOR config.env          # CALLSIGN, FREQUENCY, MODEM_PORT, reflector...

./scripts/preflight.sh      # read-only: platform, serial, OLED, conflicts
sudo ./scripts/prepare-system.sh
python3 scripts/render_configs.py
./scripts/build.sh
sudo ./scripts/install-services.sh
./scripts/status.sh
```

Or use the Makefile:

```bash
make preflight
make config
sudo make prep
make build
sudo make install
make status
```

If `prepare-system.sh` reports that the UART is not ready yet, reboot once and continue.
`prepare-system.sh` does not restart unrelated services; `install-services.sh` only
restarts the three add-on services.

## Configuration

Copy `config.example.env` to `config.env` (ignored by git) and edit it.

| Key | Meaning |
|---|---|
| `CALLSIGN` | Your callsign, 3–10 uppercase letters/digits. `N0CALL` is a placeholder; edit before transmitting. |
| `SUFFIX` | YSF node suffix, normally `ND`. |
| `FREQUENCY` | Simplex hotspot frequency in MHz, RX=TX. Public example: `438.8000`. |
| `MODEM_PORT` | `auto` (single serial device; by-id preferred) or an explicit `/dev/...` path. |
| `MODEM_BAUD` | Serial speed; 115200 is the tested MMDVM_HS value. |
| `ENABLE_OLED` | `auto` (probe at install), `0`/`no` (disable), `1`/`yes` (force). |
| `I2C_BUS` | I2C bus number for the OLED. Default `1`. |
| `I2C_ADDRESS` | OLED address, default `0x3C`. |
| `REFLECTOR_ID` | YSF reflector/room number, e.g. `46001`. |
| `REFLECTOR_COUNTRY` | Reflector country prefix, e.g. `CN`. |
| `REFLECTOR_NAME` | Reflector short name, e.g. `C4FM-TG46001` (without the country prefix). |
| `REFLECTOR_STARTUP` | Full name, must equal `COUNTRY-NAME`, e.g. `CN-C4FM-TG46001`. |
| `REFLECTOR_HOST` | Reflector IPv4 address or hostname. |
| `REFLECTOR_PORT` | Reflector UDP port, `42000` for 46001. |
| `REFLECTOR_DESC` / `HOTSPOT_NAME` / `HOTSPOT_DESC` | Display/description labels. |

### Why `Startup` must be the full name

The current YSFGateway source matches `Startup` with `findByName()`, not by numeric ID.
The hosts JSON entry becomes `COUNTRY + "-" + NAME`; therefore:

```ini
REFLECTOR_COUNTRY=CN
REFLECTOR_NAME=C4FM-TG46001
REFLECTOR_STARTUP=CN-C4FM-TG46001
```

must agree. `render_configs.py` validates this, so a gateway can no longer start with a
silent “not found” state.

### Serial ports

`MODEM_PORT=auto` prefers a single `/dev/serial/by-id/...` path, then
`/dev/ttyAMA0`, `/dev/serial0`, `/dev/ttyUSB*`, `/dev/ttyACM*`, `/dev/ttySC*`.
If more than one serial device is present, auto **refuses to guess**; set an explicit
stable by-id path:

```ini
MODEM_PORT=/dev/serial/by-id/usb-...-if00
```

The generated `MMDVM-Host.ini` contains the resolved `UARTPort`. Re-render and restart
after changing it.

### Frequency

- The example uses **438.8000 MHz**, a common UHF hotspot default. Confirm no local
  repeater/hotspot uses it and follow your national band plan.
- The author's **144.3000 MHz** 2m experiment is documented only as a field note.
  144.300 is the 2m weak-signal/SSB calling area and is unsuitable as a public default.
- First transmissions should use a dummy load or a known-good antenna. Do not use a
  430 MHz antenna at 144 MHz.

## OLED Live Caller

The OLED is optional. On the tested HAT it is an SSD1306 on Pi I2C-1 `0x3C`, driven by
the host, not by the STM32 firmware.

```text
▓ LIVE CALLER        <your call> ▓
        <speaker call>
      <country> · NET/RF
● <reflector> <frequency>   <clock>
```

- Main line: current/last speaker's callsign.
- Top right: your own callsign (`OWN_CALL`).
- Region: inferred offline from the callsign prefix; YSF frames carry no name/city.
- `NET` vs `RF`; RF also shows BER.
- After 90 seconds of silence: `STANDBY`.
- Generate a PNG without touching I2C:

```bash
python3 oled/oled_display.py --preview /tmp/oled.png \
  --own-call N0CALL --reflector 46001 --frequency 438.800 --demo-call JA1ABC
```

The service reads `/etc/mmdvm/oled.env` for `OWN_CALL`, `REFLECTOR_ID`,
`FREQUENCY_LABEL`, `I2C_BUS`, `I2C_ADDRESS`. If no OLED is found and
`ENABLE_OLED=auto`, the installer skips the OLED service; MMDVM still runs.

## How it works

```text
FT-5D / FTM-510D  (C4FM, DN mode)
        │  144/430 MHz
        ▼
MMDVM modem (STM32 HAT or USB MMDVM)
        │  UART: /dev/ttyAMA0 or /dev/ttyUSB...
        ▼
   MMDVM-Host  ── 127.0.0.1:3200/4200 ──  YSFGateway
                                            │ UDP 42000
                                            ▼
                                 YSF reflector (e.g. 46001)

SSD1306 (optional, I2C) ◄── oled_display.py ◄── /var/log/mmdvm logs
```

| Service | Role |
|---|---|
| `ysfgateway` | Connects to the reflector and bridges MMDVM-Host to YSF |
| `mmdvmhost` | Drives the modem and C4FM RF |
| `oled-display` | Reads logs and drives the optional SSD1306 |

Logs: `/var/log/mmdvm/ysfgateway.log`, `/var/log/mmdvm/mmdvmhost.log`.
Service errors remain in journald.

## Platform notes

### Raspberry Pi 5

`prepare-system.sh` adds `dtoverlay=uart0-pi5` to `/boot/firmware/config.txt`, tries to
load it at runtime, and disables a serial getty on `ttyAMA0` if present. Reboot only if
`/dev/ttyAMA0` is still missing. This is the **tested** path.

### Raspberry Pi 4 / 3 / Zero 2 W (experimental)

This path is **not hardware-tested**. `prepare-system.sh` adds `enable_uart=1` and
prefers `dtoverlay=miniuart-bt` (Bluetooth moves to the mini-UART) or falls back to
`dtoverlay=disable-bt` (Bluetooth off). It edits `/boot/firmware/config.txt` or
`/boot/config.txt`, backs it up, and requires a reboot. Verify that `/dev/ttyAMA0` is
the modem UART on your board; Bluetooth changes may be undesirable on a desktop system.

### x86_64 with a USB MMDVM modem (build/CI only; hardware NOT tested)

- A Pi GPIO HAT cannot be plugged into an x86 PC. Use a USB serial MMDVM modem.
- x86 uses **no** `/proc/device-tree/model`, `config.txt`, `dtoverlay`, `pinctrl` or
  serial-getty changes.
- Set a stable `/dev/serial/by-id/...` path in `MODEM_PORT` (auto works with one modem).
- The host builds the same MMDVM-Host/YSFGateway natively; CI proves x86_64 compilation,
  not installation or RF.
- OLED is usually unavailable; use `ENABLE_OLED=0`. An external USB-I2C display is
  untested.
- Non-apt distributions need the build dependencies installed manually.

### Other Linux / special UART bridges

Manual. A SC16IS752/SPI-UART HAT needs its own kernel overlay/driver and an explicit
`MODEM_PORT`; this project does not auto-configure such bridges. Non-systemd systems are
outside the supported install path.

## What it changes

| Adds | Does not change |
|---|---|
| packages: build tools, mosquitto/JSON dev libs, python3-pil, i2c-tools | OS or boot image |
| user `mmdvm`; serial/I2C groups | boot order or firmware |
| `/etc/mmdvm`, `/var/log/mmdvm`, `/var/lib/mmdvm` | unrelated services |
| `/usr/local/bin` binaries | desktop/network configuration |
| three systemd services | existing MMDVM/Pi-Star/WPSD (preflight refuses) |
| Pi only: `config.txt` overlay/backup; serial getty disabled | boot config on x86 (none) |

`uninstall.sh` removes the add-on files, stops the services, and restores the Pi boot
config from its backup. It does **not** remove shared packages.

## Upstream patches

`build.sh` downloads content-pinned upstream tarballs and applies two patches:

1. **`0001-ysfgateway-optional-mqtt.patch`** — upstream YSFGateway exits when no local
   MQTT broker is available; the patch lets it continue without MQTT.
2. **`0002-ysfgateway-ifstream-hosts.patch`** — upstream used `std::fstream` (read/write)
   for the hosts file; a root-owned read-only `/etc/mmdvm/YSFHosts.json` caused `EACCES`.
   The patch uses `std::ifstream`.

`upstream.lock` records URLs and SHA-256 checksums. If upstream changes, the build fails;
update the lock and re-test the patches deliberately.

## Verify

```bash
./scripts/status.sh
tail -n 20 /var/log/mmdvm/ysfgateway.log
```

Expected:

```text
Loaded 1 YSF reflectors
Linked to CN-C4FM-TG46001
```

Radio settings for the tested setup:

- frequency: the configured simplex frequency (example 438.8000 MHz)
- mode **DN**
- no CTCSS/tone
- DG-ID 0
- do not press WIRES-X (this is a direct hotspot, not a WIRES-X button flow)

## Troubleshooting

| Symptom | Check |
|---|---|
| No modem serial device | `./scripts/preflight.sh`; use `MODEM_PORT=auto` with one device, or set the by-id path. |
| No `/dev/ttyAMA0` on Pi 5 | `grep uart0-pi5 /boot/firmware/config.txt`; reboot once; check no other service owns GPIO14/15. |
| Pi 4/3 UART not working | Confirm `enable_uart=1` and the chosen BT overlay; reboot; verify `/dev/ttyAMA0` mapping. Experimental. |
| MMDVM-Host restart loop | `journalctl -u mmdvmhost`; check UART, HAT power/firmware, baud (115200 tested). |
| Gateway up but no link | Check `Startup` is the full name; check reflector host/port; inspect the gateway log. |
| `Unable to load/parse JSON file` | Confirm the ifstream patch was built and installed. |
| MQTT connection refused in logs | Harmless; no MQTT broker is required. |
| OLED blank | `ENABLE_OLED=auto`/`1`; `i2cdetect -y <bus>`; check I2C bus/address and permissions. |
| CJK boxes on OLED | Install DejaVu + Droid fallback fonts; the preview uses mixed-font rendering. |

## Uninstall

```bash
sudo ./scripts/uninstall.sh            # stop/remove add-on, restore Pi boot config
sudo ./scripts/uninstall.sh --dry-run  # print actions only
sudo ./scripts/uninstall.sh --purge    # also remove /var/log/mmdvm and mmdvm user
```

On x86 there is no boot config or GPIO overlay to restore. Shared packages are kept.

## Security and privacy

- No DMR ID, BrandMeister password, API token or MQTT broker is required.
- `config.env`, logs and build artifacts are git-ignored; the public example is `N0CALL`.
- The reflector hosts file is static; review the address yourself.
- Read `prepare-system.sh` and `install-services.sh` before running them with sudo.

## Limitations

- Only the Pi 5 configuration was tested on real hardware.
- Pi 4/3 support is experimental; x86 support is build/CI/documentation only.
- YSF only; other MMDVM modes are disabled in the generated config.
- One static reflector entry; changing reflectors requires editing and re-rendering.
- The Live Caller shows callsign + prefix-inferred country, not a name/city database.
- This is not a Pi-Star/WPSD replacement; don't install it over an existing MMDVM stack.
- No firmware flashing and no precompiled binaries/release binaries.

## GitHub access from mainland China

GitHub access from mainland China can be unstable; `git clone`/`git push` may be reset or
time out. Retry at another time, use the GitHub web "Download ZIP", or fetch in an
environment with stable access. Follow local laws and your network terms; do not use
untrusted proxies.

## Credits / License

- [G4KLX / MMDVMHost and YSFClients](https://github.com/g4klx) — GPLv2
- CA6JAU MMDVM_HS firmware
- WPSD's Live Caller style as UI inspiration (no WPSD code copied)
- DejaVu Sans Mono, Droid Sans Fallback, Pillow — system dependencies, not bundled
- Development assistance: Claude Code (Anthropic) — AI pair-programming and review; not the project author

This repository's scripts, templates and OLED code are released under **GPL-2.0**;
upstream patches remain subject to their original GPLv2 terms. See [LICENSE](LICENSE).

Author / maintainer: **BI2RIP**. Not affiliated with Yaesu, G4KLX, CA6JAU or WPSD.
