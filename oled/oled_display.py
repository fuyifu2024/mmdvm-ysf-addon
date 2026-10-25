#!/usr/bin/env python3
"""WPSD-style live-caller OLED display for an MMDVM hotspot.

Supports the common I2C OLED panels found on hotspot HATs:

  controller  SSD1306 / SSD1309 / SSD1315 (aliases) and SH1106
  size        128x64 (0.96"), 128x32 (0.91") and 64x48 (small)
  rotation    normal or 180 degrees (some HATs mount the panel upside down)

The STM32 firmware does not drive the panel, so the host draws it directly.
The default is the tested 128x64 SSD1306 at I2C-1 0x3C; set OLED_CONTROLLER,
OLED_SIZE, OLED_ROTATE and OLED_CONTRAST in config.env for other panels.

Display (128x64):
  header bar:  LIVE CALLER            <your call sign>
  main:        callsign of the current/last speaker
  region line: country inferred from the call-sign prefix + NET/RF + BER
  bottom:      link dot, reflector number, frequency, local clock

128x32 and 64x48 use a compact three-line layout (header/call/region).

OWN_CALL, REFLECTOR_ID, FREQUENCY_LABEL, I2C_BUS, I2C_ADDRESS and the OLED_*
settings are read from the environment (systemd EnvironmentFile), with
--own-call/--reflector/--frequency/--bus/--address/--controller/--size/
--rotate/--contrast overrides. --preview renders a PNG without opening I2C and
is safe for CI/docs.

YSF frames carry only the call sign (no name/city), so the region comes from
an offline call-prefix table.
"""

from __future__ import annotations

import argparse
import fcntl
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH = 128
HEIGHT = 64
I2C_SLAVE = 0x0703


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)), 0)
    except ValueError:
        return default


OWN_CALL = os.environ.get("OWN_CALL", "N0CALL").upper()
REFLECTOR = os.environ.get("REFLECTOR_ID", "46001")
FREQUENCY = os.environ.get("FREQUENCY_LABEL", "438.800")
DEFAULT_I2C_BUS = _env_int("I2C_BUS", 1)
DEFAULT_I2C_ADDRESS = _env_int("I2C_ADDRESS", 0x3C)
DEFAULT_CONTROLLER = os.environ.get("OLED_CONTROLLER", "ssd1306").strip().lower() or "ssd1306"
DEFAULT_SIZE = os.environ.get("OLED_SIZE", "128x64").strip().lower() or "128x64"
DEFAULT_ROTATE = _env_int("OLED_ROTATE", 0)
DEFAULT_CONTRAST = _env_int("OLED_CONTRAST", 0xCF)
DEFAULT_LOG_DIR = Path(os.environ.get("MMDVM_LOG_DIR", "/var/log/mmdvm"))
SIZE_MAP = {"128x64": (128, 64), "128x32": (128, 32), "64x48": (64, 48)}
CONTROLLER_ALIASES = {
    "auto": "ssd1306", "ssd1306": "ssd1306", "ssd1309": "ssd1306",
    "ssd1315": "ssd1306", "sh1106": "sh1106",
}
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
FONT_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
FONT_CJK = "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"
CALLER_HOLD_SECONDS = 90


class I2CDisplay:
    def __init__(self, bus: int = 1, address: int = 0x3C, contrast: int = 0xCF,
                 controller: str = "ssd1306", width: int = 128, height: int = 64,
                 rotate: int = 0):
        self.address = address
        self.controller = controller
        self.width = width
        self.height = height
        self.rotate = rotate
        self.offset = 2 if controller == "sh1106" else 0
        self.fd = os.open(f"/dev/i2c-{bus}", os.O_RDWR)
        fcntl.ioctl(self.fd, I2C_SLAVE, address)
        self._init(contrast)
        self.clear()

    def _init(self, contrast: int):
        sequence = [0xAE, 0xD5, 0x80, 0xA8, self.height - 1, 0xD3, 0x00, 0x40]
        if self.controller == "sh1106":
            sequence += [
                0xAD, 0x8B,
                0xA1, 0xC8,
                0xDA, 0x12,
                0x81, contrast,
                0xD9, 0xF1,
                0xDB, 0x40,
                0xA4, 0xA6, 0x2E, 0xAF,
            ]
        else:
            com_pins = 0x02 if self.height == 32 else 0x12
            sequence += [
                0x8D, 0x14,
                0x20, 0x00,
                0xA1, 0xC8,
                0xDA, com_pins,
                0x81, contrast,
                0xD9, 0xF1,
                0xDB, 0x40,
                0xA4, 0xA6, 0x2E, 0xAF,
            ]
        self._commands(sequence)

    def _write(self, payload):
        os.write(self.fd, bytes(payload))

    def _commands(self, commands):
        self._write([0x00] + list(commands))

    def _framebuffer(self, image: Image.Image) -> bytearray:
        image = image.convert("1")
        if image.size != (self.width, self.height):
            image = image.resize((self.width, self.height))
        if self.rotate == 180:
            image = image.transpose(Image.ROTATE_180)
        pixels = image.load()
        buffer = bytearray(self.width * self.height // 8)
        for y in range(self.height):
            page = y // 8
            bit = 1 << (y % 8)
            base = page * self.width
            for x in range(self.width):
                if pixels[x, y]:
                    buffer[base + x] |= bit
        return buffer

    def show(self, image: Image.Image):
        buffer = self._framebuffer(image)
        if self.controller == "sh1106":
            # SH1106 uses page addressing and has a 132-column RAM; most
            # 1.3" modules expose the visible 128 columns at offset 2.
            for page in range(self.height // 8):
                column = self.offset
                self._commands([
                    0xB0 + page,
                    0x00 + (column & 0x0F),
                    0x10 + ((column >> 4) & 0x0F),
                ])
                chunk = buffer[page * self.width:(page + 1) * self.width]
                for offset in range(0, len(chunk), 32):
                    self._write([0x40] + list(chunk[offset:offset + 32]))
        else:
            self._commands([0x21, 0, self.width - 1, 0x22, 0, (self.height // 8) - 1])
            for offset in range(0, len(buffer), 32):
                self._write([0x40] + list(buffer[offset:offset + 32]))

    def clear(self):
        self.show(Image.new("1", (self.width, self.height), 0))

    def close(self):
        try:
            os.close(self.fd)
        except OSError:
            pass


def _tail(path: Path, limit: int = 65536) -> list[str]:
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - limit))
            data = f.read()
    except OSError:
        return []
    return data.decode("utf-8", "replace").splitlines()


_TS_RE = re.compile(r"^[A-Z]: (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})")
_RX_END_RE = re.compile(
    r"YSF, received RF end of transmission from (.+?)\s+to DG-ID \d+"
    r".*?BER: ([\d.]+)%, RSSI: (-?\d+)/(-?\d+)/(-?\d+)"
)
_RX_HEADER_RE = re.compile(r"YSF, received RF header from (.+?)\s+to DG-ID \d+")
_NET_DATA_RE = re.compile(
    r"YSF, received network data from (.+?)\s+to DG-ID \d+ at (\S+)"
)
_NET_END_RE = re.compile(
    r"YSF, received network end of transmission from (.+?)\s+to DG-ID \d+"
    r" at (\S+),\s+([\d.]+) seconds,\s+(\d+)% packet loss"
)
_LINK_RE = re.compile(
    r"(Linked to (.+?)\s*$|Link lost|Link failed|Disconnected|Unlinked)"
)


def _line_time(line: str):
    match = _TS_RE.match(line)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S.%f").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def _process_running(name: str) -> bool:
    return subprocess.run(
        ["pgrep", "-x", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    ).returncode == 0


def _region(call: str) -> str:
    c = re.sub(r"[^A-Z0-9]", "", call.upper())
    if not c:
        return "未知地区"

    special = [
        ("VR2", "香港"), ("XX9", "澳门"), ("BV", "台湾"), ("BU", "台湾"), ("BX", "台湾"),
        ("A6", "阿联酋"), ("AP", "巴基斯坦"), ("A4", "阿曼"), ("A7", "卡塔尔"), ("A9", "巴林"),
        ("HL", "韩国"), ("DS", "韩国"), ("DT", "韩国"),
        ("6K", "韩国"), ("6L", "韩国"), ("6M", "韩国"), ("6N", "韩国"),
        ("VE", "加拿大"), ("VA", "加拿大"), ("VY", "加拿大"),
        ("VK", "澳大利亚"), ("ZL", "新西兰"),
        ("EA", "西班牙"), ("EB", "西班牙"), ("EC", "西班牙"), ("ED", "西班牙"),
        ("EE", "西班牙"), ("EF", "西班牙"), ("EG", "西班牙"), ("EH", "西班牙"),
        ("AM", "西班牙"), ("AN", "西班牙"), ("AO", "西班牙"),
        ("ON", "比利时"), ("OO", "比利时"), ("OP", "比利时"), ("OQ", "比利时"),
        ("OR", "比利时"), ("OS", "比利时"), ("OT", "比利时"),
        ("PA", "荷兰"), ("PB", "荷兰"), ("PC", "荷兰"), ("PD", "荷兰"), ("PE", "荷兰"),
        ("PF", "荷兰"), ("PG", "荷兰"), ("PH", "荷兰"), ("PI", "荷兰"),
        ("HB", "瑞士"), ("HE", "瑞士"), ("OE", "奥地利"),
        ("OH", "芬兰"), ("OF", "芬兰"), ("OG", "芬兰"), ("OI", "芬兰"),
        ("LA", "挪威"), ("LB", "挪威"), ("LC", "挪威"), ("LD", "挪威"), ("LE", "挪威"),
        ("LF", "挪威"), ("LG", "挪威"), ("LH", "挪威"), ("LI", "挪威"), ("LJ", "挪威"),
        ("LK", "挪威"), ("LL", "挪威"), ("LM", "挪威"), ("LN", "挪威"),
        ("OZ", "丹麦"),
        ("SA", "瑞典"), ("SB", "瑞典"), ("SC", "瑞典"), ("SD", "瑞典"), ("SE", "瑞典"),
        ("SF", "瑞典"), ("SG", "瑞典"), ("SH", "瑞典"), ("SI", "瑞典"), ("SJ", "瑞典"),
        ("SK", "瑞典"), ("SL", "瑞典"), ("SM", "瑞典"), ("7S", "瑞典"), ("8S", "瑞典"),
        ("SP", "波兰"), ("SQ", "波兰"), ("SN", "波兰"), ("SO", "波兰"), ("3Z", "波兰"),
        ("OK", "捷克"), ("OL", "捷克"),
        ("DU", "菲律宾"), ("DV", "菲律宾"), ("DW", "菲律宾"), ("DX", "菲律宾"),
        ("DY", "菲律宾"), ("DZ", "菲律宾"),
        ("DA", "德国"), ("DB", "德国"), ("DC", "德国"), ("DD", "德国"), ("DE", "德国"),
        ("DF", "德国"), ("DG", "德国"), ("DH", "德国"), ("DI", "德国"), ("DJ", "德国"),
        ("DK", "德国"), ("DL", "德国"), ("DM", "德国"), ("DO", "德国"), ("DP", "德国"),
        ("DQ", "德国"), ("DR", "德国"),
        ("UR", "乌克兰"), ("US", "乌克兰"), ("UT", "乌克兰"), ("UU", "乌克兰"),
        ("UV", "乌克兰"), ("UW", "乌克兰"), ("UX", "乌克兰"), ("UY", "乌克兰"), ("UZ", "乌克兰"),
        ("PP", "巴西"), ("PQ", "巴西"), ("PR", "巴西"), ("PS", "巴西"), ("PT", "巴西"),
        ("PU", "巴西"), ("PV", "巴西"), ("PW", "巴西"), ("PX", "巴西"), ("PY", "巴西"),
        ("ZV", "巴西"), ("ZW", "巴西"), ("ZX", "巴西"), ("ZY", "巴西"), ("ZZ", "巴西"),
        ("LU", "阿根廷"), ("LO", "阿根廷"), ("LP", "阿根廷"), ("LQ", "阿根廷"),
        ("LR", "阿根廷"), ("LS", "阿根廷"), ("LT", "阿根廷"), ("LW", "阿根廷"),
        ("ZS", "南非"), ("ZR", "南非"), ("ZT", "南非"), ("ZU", "南非"),
        ("VU", "印度"), ("AT", "印度"),
        ("HS", "泰国"), ("E2", "泰国"),
        ("9V", "新加坡"), ("S6", "新加坡"),
        ("9M", "马来西亚"), ("9W", "马来西亚"),
        ("4X", "以色列"), ("4Z", "以色列"),
        ("HZ", "沙特阿拉伯"), ("7Z", "沙特阿拉伯"),
        ("TA", "土耳其"), ("TC", "土耳其"),
        ("YV", "委内瑞拉"), ("YW", "委内瑞拉"), ("YX", "委内瑞拉"), ("YY", "委内瑞拉"),
        ("YB", "印度尼西亚"), ("YC", "印度尼西亚"), ("YD", "印度尼西亚"),
        ("YE", "印度尼西亚"), ("YF", "印度尼西亚"), ("YG", "印度尼西亚"),
        ("YH", "印度尼西亚"),
        ("XE", "墨西哥"), ("XF", "墨西哥"), ("XG", "墨西哥"), ("XH", "墨西哥"),
        ("XI", "墨西哥"),
        ("YA", "阿富汗"), ("YI", "伊拉克"), ("YK", "叙利亚"), ("YO", "罗马尼亚"),
        ("YT", "塞尔维亚"), ("YU", "塞尔维亚"),
        ("OA", "秘鲁"), ("OB", "秘鲁"), ("OC", "秘鲁"),
        ("CP", "玻利维亚"), ("HC", "厄瓜多尔"), ("HD", "厄瓜多尔"),
        ("CX", "乌拉圭"), ("ZP", "巴拉圭"),
        ("S2", "孟加拉国"), ("9N", "尼泊尔"), ("4S", "斯里兰卡"),
        ("EP", "伊朗"), ("JY", "约旦"), ("OD", "黎巴嫩"), ("5B", "塞浦路斯"),
        ("9A", "克罗地亚"), ("S5", "斯洛文尼亚"), ("YT", "塞尔维亚"),
        ("LZ", "保加利亚"), ("OM", "斯洛伐克"), ("9H", "马耳他"),
        ("5N", "尼日利亚"), ("5Z", "肯尼亚"),
    ]
    for prefix, name in special:
        if c.startswith(prefix):
            return name

    if c.startswith("B"):
        return "中国"
    if c.startswith("J"):
        return "日本"
    if c.startswith("A") or c.startswith(("K", "N", "W")):
        return "美国"
    if c.startswith(("G", "M", "2E", "2M", "2W")):
        return "英国"
    if c.startswith("F"):
        return "法国"
    if c.startswith("I"):
        return "意大利"
    if c.startswith("D"):
        return "德国"
    if c.startswith("R") or c.startswith(("UA", "UB", "UC", "UD", "UE", "UF",
                                           "UG", "UH", "UI")):
        return "俄罗斯"
    return "未知地区"


def collect_status(gateway_log: Path, host_log: Path, demo_call: str | None = None) -> dict:
    status = {
        "gateway_running": _process_running("YSFGateway"),
        "host_running": _process_running("MMDVM-Host"),
        "linked": False,
        "active": False,
        "speaker": None,
        "source": None,
        "ber": None,
        "rssi": None,
        "event_time": None,
    }

    for line in reversed(_tail(gateway_log)):
        match = _LINK_RE.search(line)
        if match:
            if match.group(2):
                status["linked"] = True
            break

    if demo_call:
        status.update(
            active=True,
            speaker=demo_call.upper(),
            source="NET",
            event_time=datetime.now(timezone.utc),
        )
        return status

    for line in reversed(_tail(host_log)):
        event_time = _line_time(line)
        match = _NET_END_RE.search(line)
        if match:
            status.update(active=True, speaker=match.group(1).strip(),
                          source="NET", event_time=event_time)
            break
        match = _NET_DATA_RE.search(line)
        if match:
            status.update(active=True, speaker=match.group(1).strip(),
                          source="NET", event_time=event_time)
            break
        match = _RX_END_RE.search(line)
        if match:
            status.update(active=True, speaker=match.group(1).strip(),
                          source="RF", ber=float(match.group(2)),
                          rssi=int(match.group(3)), event_time=event_time)
            break
        match = _RX_HEADER_RE.search(line)
        if match:
            status.update(active=True, speaker=match.group(1).strip(),
                          source="RF", event_time=event_time)
            break

    if status["event_time"] is not None:
        age = (datetime.now(timezone.utc) - status["event_time"]).total_seconds()
        if age > CALLER_HOLD_SECONDS or not status["host_running"]:
            status["active"] = False
            status["speaker"] = None
            status["source"] = None
            status["ber"] = None
            status["rssi"] = None

    return status


def _font(path: str, size: int):
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        # Keep the display usable on a minimal system without the optional
        # DejaVu/Droid fonts.  The bitmap fallback has no CJK glyphs.
        return ImageFont.load_default()


def _center_text(draw, text, font, y, fill=1):
    width = draw.textlength(text, font=font)
    draw.text(((WIDTH - width) / 2, y), text, font=font, fill=fill)


def _fit(draw, text: str, font, max_width: int):
    if draw.textlength(text, font=font) <= max_width:
        return text
    for length in range(len(text) - 1, 0, -1):
        candidate = text[:length]
        if draw.textlength(candidate + "…", font=font) <= max_width:
            return candidate + "…"
    return ""


def _fit_font(draw, text: str, path: str, sizes, max_width=124):
    for size in sizes:
        font = _font(path, size)
        if draw.textlength(text, font=font) <= max_width:
            return font
    return _font(path, sizes[-1])


def _draw_mixed(draw, left: str, right: str, font_left, font_right, y: int,
                sep: str = " · ") -> None:
    """Draw CJK text with a CJK font and the ASCII detail with DejaVu.

    The fallback CJK font does not cover the ASCII glyphs used here; mixing
    the fonts avoids the tofu boxes after the country name.
    """
    width_left = draw.textlength(left, font=font_left)
    width_sep = draw.textlength(sep, font=font_right)
    width_right = draw.textlength(right, font=font_right)
    total = width_left + width_sep + width_right
    if total > WIDTH - 4:
        right = _fit(draw, right, font_right, max(10, WIDTH - 4 - width_left - width_sep))
        width_right = draw.textlength(right, font=font_right)
        total = width_left + width_sep + width_right

    x = (WIDTH - total) / 2
    draw.text((x, y), left, font=font_left, fill=1, anchor="lm")
    draw.text((x + width_left, y), sep, font=font_right, fill=1, anchor="lm")
    draw.text((x + width_left + width_sep, y), right, font=font_right, fill=1,
              anchor="lm")


def render(status: dict, fonts) -> Image.Image:
    font_header, font_big, font_mid, font_small, font_bottom, font_cjk = fonts
    image = Image.new("1", (WIDTH, HEIGHT), 0)
    draw = ImageDraw.Draw(image)

    active = status["active"] and status["speaker"]

    if HEIGHT >= 60:
        header = "LIVE CALLER" if active else "STATION IDLE"
        draw.rectangle((0, 0, WIDTH - 1, 11), fill=1)
        draw.text((2, 0), header, font=font_header, fill=0)
        own_w = draw.textlength(OWN_CALL, font=font_header)
        draw.text((WIDTH - 3 - own_w, 0), OWN_CALL, font=font_header, fill=0)

        if active:
            speaker = status["speaker"]
            big = _fit_font(draw, speaker, FONT_BOLD, (20, 18, 16, 14, 12))
            _center_text(draw, speaker, big, 13)

            region = _region(speaker)
            detail = status["source"]
            if status["source"] == "RF" and status["ber"] is not None:
                detail += f" {status['ber']:.1f}%"
            _draw_mixed(draw, region, detail, font_cjk, font_mid, 43)
        else:
            _center_text(draw, "STANDBY", font_big, 17)
            _center_text(draw, "MMDVM_HS v1.5.2", font_small, 39)

        clock = datetime.now().strftime("%H:%M:%S")
        clock_w = draw.textlength(clock, font=font_bottom)
        if not status["gateway_running"]:
            link_text = f"{REFLECTOR} OFFLINE"
            linked = False
        elif status["linked"]:
            link_text = f"{REFLECTOR} {FREQUENCY}"
            linked = True
        else:
            link_text = f"{REFLECTOR} LINKING"
            linked = False

        draw.ellipse((2, 54, 6, 58), fill=1 if linked else 0,
                     outline=1 if not linked else None)
        draw.text((9, 51), link_text, font=font_bottom, fill=1)
        draw.text((WIDTH - 3 - clock_w, 51), clock, font=font_bottom, fill=1)
        return image

    # Compact 128x32 and 64x48 layouts.
    bar_h = 9
    draw.rectangle((0, 0, WIDTH - 1, bar_h - 1), fill=1)
    label = "LIVE" if active else "IDLE"
    draw.text((1, 0), label, font=font_header, fill=0)
    own_w = draw.textlength(OWN_CALL, font=font_header)
    draw.text((WIDTH - 2 - own_w, 0), OWN_CALL, font=font_header, fill=0)

    if active:
        speaker = status["speaker"]
        big = _fit_font(draw, speaker, FONT_BOLD, (12, 11, 10, 9, 8), WIDTH - 2)
        _center_text(draw, speaker, big, bar_h)
        region = _region(speaker)
        detail = status["source"]
        if status["source"] == "RF" and status["ber"] is not None:
            detail += f" {status['ber']:.1f}%"
        region_y = 26 if HEIGHT <= 32 else 28
        _draw_mixed(draw, region, detail, font_cjk, font_mid, region_y)
    else:
        _center_text(draw, "STANDBY", font_big, bar_h)
        if HEIGHT <= 32:
            _center_text(draw, f"{REFLECTOR} {FREQUENCY}", font_bottom, HEIGHT - 8)
        else:
            _center_text(draw, FREQUENCY, font_bottom, 24)

    if HEIGHT >= 40:
        short_clock = datetime.now().strftime("%H:%M")
        if not status["gateway_running"]:
            linked = False
        else:
            linked = status["linked"]
        draw.ellipse((1, HEIGHT - 7, 4, HEIGHT - 4), fill=1 if linked else 0,
                     outline=1 if not linked else None)
        draw.text((6, HEIGHT - 9), REFLECTOR, font=font_bottom, fill=1)
        draw.text((WIDTH - 2 - draw.textlength(short_clock, font=font_bottom),
                   HEIGHT - 9), short_clock, font=font_bottom, fill=1)

    return image


def main() -> int:
    global OWN_CALL, REFLECTOR, FREQUENCY, WIDTH, HEIGHT

    parser = argparse.ArgumentParser(
        description="MMDVM YSF live-caller OLED (SSD1306/SH1106, several sizes)")
    parser.add_argument("--bus", type=int, default=DEFAULT_I2C_BUS,
                        help="I2C bus number (default from I2C_BUS)")
    parser.add_argument("--address", type=lambda x: int(x, 0),
                        default=DEFAULT_I2C_ADDRESS,
                        help="I2C address (default from I2C_ADDRESS)")
    parser.add_argument("--controller", default=DEFAULT_CONTROLLER,
                        help="ssd1306 (default; ssd1309/ssd1315 are aliases) or sh1106")
    parser.add_argument("--size", default=DEFAULT_SIZE,
                        choices=sorted(SIZE_MAP),
                        help="panel size: 128x64, 128x32 or 64x48")
    parser.add_argument("--rotate", type=int, default=DEFAULT_ROTATE,
                        choices=(0, 180), help="rotate the panel 180 degrees")
    parser.add_argument("--contrast", type=lambda x: int(x, 0),
                        default=DEFAULT_CONTRAST)
    parser.add_argument("--gateway-log", type=Path,
                        default=DEFAULT_LOG_DIR / "ysfgateway.log")
    parser.add_argument("--host-log", type=Path,
                        default=DEFAULT_LOG_DIR / "mmdvmhost.log")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--own-call", help="override OWN_CALL")
    parser.add_argument("--reflector", help="override REFLECTOR_ID")
    parser.add_argument("--frequency", help="override FREQUENCY_LABEL")
    parser.add_argument("--demo-call", help="force a caller for preview")
    parser.add_argument("--preview", type=Path,
                        help="render a PNG preview and exit without touching I2C")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--clear", action="store_true")
    args = parser.parse_args()

    controller = CONTROLLER_ALIASES.get(args.controller.strip().lower())
    if controller is None:
        parser.error("controller must be ssd1306/ssd1309/ssd1315/sh1106/auto")
    size = args.size.strip().lower()
    if size not in SIZE_MAP:
        parser.error("size must be one of: " + ", ".join(sorted(SIZE_MAP)))
    if controller == "sh1106" and size != "128x64":
        parser.error("SH1106 support here is limited to 128x64")
    if args.rotate not in (0, 180):
        parser.error("rotate must be 0 or 180")
    if not 0 <= args.contrast <= 0xFF:
        parser.error("contrast must be 0x00-0xFF")

    OWN_CALL = (args.own_call or OWN_CALL).upper()
    if args.reflector:
        REFLECTOR = args.reflector
    if args.frequency:
        FREQUENCY = args.frequency
    WIDTH, HEIGHT = SIZE_MAP[size]

    if HEIGHT >= 60:
        fonts = (
            _font(FONT_BOLD, 9),
            _font(FONT_BOLD, 16),
            _font(FONT_BOLD, 10),
            _font(FONT_REGULAR, 9),
            _font(FONT_REGULAR, 8),
            _font(FONT_CJK, 11),
        )
    elif HEIGHT <= 32:
        fonts = (
            _font(FONT_BOLD, 7),
            _font(FONT_BOLD, 12),
            _font(FONT_BOLD, 8),
            _font(FONT_REGULAR, 7),
            _font(FONT_REGULAR, 7),
            _font(FONT_CJK, 9),
        )
    else:
        fonts = (
            _font(FONT_BOLD, 7),
            _font(FONT_BOLD, 12),
            _font(FONT_BOLD, 8),
            _font(FONT_REGULAR, 7),
            _font(FONT_REGULAR, 7),
            _font(FONT_CJK, 9),
        )

    def frame():
        status = collect_status(args.gateway_log, args.host_log, args.demo_call)
        return render(status, fonts)

    if args.preview:
        status = collect_status(args.gateway_log, args.host_log, args.demo_call)
        status.update(gateway_running=True, host_running=True, linked=True)
        image = render(status, fonts)
        args.preview.parent.mkdir(parents=True, exist_ok=True)
        image.resize((WIDTH * 4, HEIGHT * 4), 0).save(args.preview)
        print(f"wrote {args.preview} ({WIDTH}x{HEIGHT}, {controller})")
        return 0

    try:
        display = I2CDisplay(args.bus, args.address, args.contrast,
                             controller=controller, width=WIDTH, height=HEIGHT,
                             rotate=args.rotate)
    except OSError as exc:
        print(f"OLED: cannot open /dev/i2c-{args.bus} at 0x{args.address:02X}: {exc}",
              file=sys.stderr)
        return 1

    try:
        if args.clear:
            display.clear()
            return 0

        if args.once:
            display.show(frame())
            return 0

        while True:
            display.show(frame())
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0
    finally:
        display.close()


if __name__ == "__main__":
    raise SystemExit(main())
