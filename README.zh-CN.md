# mmdvm-ysf-addon

**[English](README.md) | 简体中文**

**在你已经在用的 Linux 机器上顺手加一个 MMDVM YSF 热点——不换系统、不刷独立镜像、可以卸载回滚。**

![OLED Live Caller](docs/oled-live-caller.png)

---

## 这是什么

很多 MMDVM 热点方案要求刷专用镜像（Pi-Star / WPSD），把整台机器变成热点机。
这个项目走另一条路：**它是现有机器上的 add-on**，适合家里已经在跑 Ubuntu/Debian
的服务器、桌面 Pi 或电台电脑。

它会增加：

- 独立的 `mmdvm` 系统用户
- 三个 systemd 服务：`ysfgateway`、`mmdvmhost`、`oled-display`
- `/etc/mmdvm`、`/var/log/mmdvm`、`/var/lib/mmdvm`
- `/usr/local/bin/{MMDVM-Host,YSFGateway,oled_display.py}`
- Raspberry Pi 上的 boot overlay（Pi 5 用 `uart0-pi5`；Pi 4/3 用
  `enable_uart` + BT overlay，实验性）
- 构建/运行依赖（见下文）

它**不会**替换 OS、改启动顺序、刷 HAT 固件、重装桌面或接管无关服务。x86 这类没有
Pi GPIO 的平台**完全不会改 boot 配置**。

> 老实说：它**不是零改动**。会安装包、增加用户/目录/三个服务，在 Pi 上还会改
> boot 配置。所以项目提供 preflight 冲突检查和 uninstall 回滚。下面“会改什么”
> 表列清楚每一项。

## 平台支持

| 平台 | 状态 | 调制解调器连接 | OLED |
|---|---|---|---|
| **Raspberry Pi 5 + Ubuntu 26.04** | **作者实机验证** | GPIO14/15 HAT，`dtoverlay=uart0-pi5` | Pi I2C-1 0x3C（实测） |
| Raspberry Pi 4 / 3 / Zero 2 W | **实验性，未实机验证** | GPIO14/15 HAT，`enable_uart=1` + `miniuart-bt`/`disable-bt`，需重启 | Pi I2C，理论可用 |
| x86_64 Ubuntu / Debian | **仅构建/CI，硬件未测** | **USB 串口 MMDVM 板**（ttyUSB/ttyACM）；Pi GPIO HAT 不能直接插 | 通常没有；外接 I2C 未验证 |
| 其它 Linux / 特殊 UART 桥 | 手动/不支持 | USB 串口；自行设置 `MODEM_PORT` | 可选/手动 |

CI 只能证明上游源码、补丁和本项目能在 x86_64 GitHub runner 上构建；**不能**证明
UART、USB 枚举、I2C、systemd 安装、射频或反射器 link。只有下面的 Pi 5 组合在真实
硬件上验证过。

## 已验证环境

- Raspberry Pi 5 Model B，**Ubuntu 26.04.1 LTS (arm64)**
- MMDVM_HS_Hat 固件 `MMDVM_HS_Hat-v1.5.2 20201108 14.7456MHz ADF7021 FW by CA6JAU`
- 板载 SSD1306 OLED，Pi I2C-1 `0x3C`
- YSF 反射器 `46001 / CN-C4FM-TG46001`（`222.128.4.82:42000`）
- 作者：BI2RIP（FT-5D / FTM-510D）

作者在同一块 HAT 上实测过 **144.3000 MHz 2m** 配置；公开示例故意使用
**438.8000 MHz**。144.300 是 2m 弱信号/SSB 呼叫区域，**不建议**作为热点默认频点，
详见频率说明。

## 前提

- 一台现有 Linux 机器；Ubuntu/Debian + systemd 是支持的安装路径。
- Pi：兼容的 MMDVM HAT，STM32 固件已刷好，GPIO14/15 空闲。
- x86：**USB 串口 MMDVM 调制解调器**（或 USB-TTL 接到调制解调器）。
- 有效业余呼号，以及当地协调好的频率。
- 可选 OLED 需要 Python 3 + Pillow（`python3-pil`）。
- 机器上不能已有 MMDVM / Pi-Star / WPSD；preflight 会拒绝覆盖。
- 本项目**不**刷固件、不提供预编译镜像。

## 快速开始

```bash
git clone https://github.com/fuyifu2024/mmdvm-ysf-addon.git
cd mmdvm-ysf-addon
./setup.sh
```

推荐直接用 `setup.sh`。它会：

- 没有 `config.env` 时交互询问呼号、频率、`MODEM_PORT`
- 拒绝 `N0CALL`（必须填真实呼号）
- 先跑 preflight，再用 sudo 准备 UART/依赖/用户组
- 渲染配置并构建上游（内存低时自动用 `JOBS=1`）
- 安装并启动服务，最后打印状态

常用选项：

```bash
./setup.sh --dry-run          # 只打印计划，不修改
./setup.sh --yes              # 跳过最后确认
./setup.sh --non-interactive  # 要求已编辑好 config.env，不交互
./setup.sh --rebuild          # 强制重新编译
```

Pi 4/3 可能提示重启；重启后重新运行 `./setup.sh`，配置和系统准备会保留。x86 没找到
串口时，接上 USB MMDVM 调制解调器再重跑。`prepare-system.sh` 不会重启无关服务；
`install-services.sh` 只重启本 add-on 的三个服务。

### 手工 / 分步安装

```bash
cp config.example.env config.env
$EDITOR config.env          # CALLSIGN、FREQUENCY、MODEM_PORT、反射器...

./scripts/preflight.sh      # 只读：平台、串口、OLED、冲突
sudo ./scripts/prepare-system.sh
python3 scripts/render_configs.py
./scripts/build.sh
sudo ./scripts/install-services.sh
./scripts/status.sh
```

也可以用 Makefile：

```bash
make preflight
make config
sudo make prep
make build
sudo make install
make status
```

如果 `prepare-system.sh` 提示 UART 还没准备好，重启一次再继续。

## 配置

复制 `config.example.env` 到 `config.env`（已被 git 忽略）后编辑。

| 键 | 说明 |
|---|---|
| `CALLSIGN` | 呼号，3–10 位大写字母/数字。`N0CALL` 只是占位，发射前必须改。 |
| `SUFFIX` | YSF 节点后缀，通常 `ND`。 |
| `FREQUENCY` | 单频热点频率（MHz），RX=TX。公开示例 `438.8000`。 |
| `MODEM_PORT` | `auto`（只允许一个串口，优先 by-id）或显式 `/dev/...` 路径。 |
| `MODEM_BAUD` | 串口速率；115200 是实测的 MMDVM_HS 值。 |
| `ENABLE_OLED` | `auto`（安装时探测）、`0`/`no`（关闭）、`1`/`yes`（强制）。 |
| `I2C_BUS` | OLED 的 I2C 总线号，默认 `1`。 |
| `I2C_ADDRESS` | OLED 地址，默认 `0x3C`。 |
| `REFLECTOR_ID` | YSF 反射器/房间号，例如 `46001`。 |
| `REFLECTOR_COUNTRY` | 反射器国家前缀，例如 `CN`。 |
| `REFLECTOR_NAME` | 反射器短名，例如 `C4FM-TG46001`（不要带国家前缀）。 |
| `REFLECTOR_STARTUP` | 完整名，必须等于 `COUNTRY-NAME`，例如 `CN-C4FM-TG46001`。 |
| `REFLECTOR_HOST` | 反射器 IPv4 地址或主机名。 |
| `REFLECTOR_PORT` | 反射器 UDP 端口，46001 是 `42000`。 |
| `REFLECTOR_DESC` / `HOTSPOT_NAME` / `HOTSPOT_DESC` | 显示/描述标签。 |

### 为什么 `Startup` 必须写完整名

当前 YSFGateway 用 `findByName()` 匹配 `Startup`，不是按数字 ID。hosts JSON 条目的
完整名是 `COUNTRY + "-" + NAME`，所以：

```ini
REFLECTOR_COUNTRY=CN
REFLECTOR_NAME=C4FM-TG46001
REFLECTOR_STARTUP=CN-C4FM-TG46001
```

三者必须一致。`render_configs.py` 会校验，避免网关“启动了但没 link 上”。

### 串口

`MODEM_PORT=auto` 优先选择单个 `/dev/serial/by-id/...`，然后依次考虑
`/dev/ttyAMA0`、`/dev/serial0`、`/dev/ttyUSB*`、`/dev/ttyACM*`、`/dev/ttySC*`。
如果系统有多个串口设备，auto **拒绝猜**；请显式写稳定路径：

```ini
MODEM_PORT=/dev/serial/by-id/usb-...-if00
```

生成的 `MMDVM-Host.ini` 会写入解析后的 `UARTPort`。改端口后重新渲染并重启。

### 频率

- 示例用 **438.8000 MHz**，常见 UHF 热点默认值；仍需确认当地没有同频中继/热点，
  并遵守所在国家的频段规划。
- 作者的 **144.3000 MHz** 2m 实验只作为实测记录保留。144.300 是 2m 弱信号/SSB
  呼叫区域，不适合作为公开默认。
- 首次发射建议接 50Ω 假负载或确认驻波；不要用 430 MHz 天线在 144 MHz 发射。

## OLED Live Caller

OLED 是可选功能。实测 HAT 上是 Pi I2C-1 `0x3C` 的 SSD1306，由 Pi 主机侧驱动，
不是 STM32 固件驱动。

```text
▓ LIVE CALLER        <你的呼号> ▓
        <当前发射者呼号>
      <国家> · NET/RF
● <反射器> <频率>   <时间>
```

- 主位：当前/最近发射者呼号。
- 右上角：你自己的呼号（`OWN_CALL`）。
- 地区：按呼号前缀离线推断；YSF 帧不带姓名/城市。
- `NET` 表示网络来源，`RF` 表示本机射频；RF 显示 BER。
- 90 秒无通话显示 `STANDBY`。
- 生成 PNG 预览（不访问 I2C）：

```bash
python3 oled/oled_display.py --preview /tmp/oled.png \
  --own-call N0CALL --reflector 46001 --frequency 438.800 --demo-call JA1ABC
```

OLED 服务从 `/etc/mmdvm/oled.env` 读取 `OWN_CALL`、`REFLECTOR_ID`、
`FREQUENCY_LABEL`、`I2C_BUS`、`I2C_ADDRESS`。如果 `ENABLE_OLED=auto` 且没找到屏幕，
安装器会跳过 OLED 服务；MMDVM 仍正常运行。

## 工作原理

```text
FT-5D / FTM-510D  (C4FM, DN)
        │  144/430 MHz
        ▼
MMDVM 调制解调器（STM32 HAT 或 USB MMDVM）
        │  UART: /dev/ttyAMA0 或 /dev/ttyUSB...
        ▼
   MMDVM-Host  ── 127.0.0.1:3200/4200 ──  YSFGateway
                                            │ UDP 42000
                                            ▼
                                 YSF 反射器（例如 46001）

SSD1306（可选，I2C） ◄── oled_display.py ◄── /var/log/mmdvm 日志
```

| 服务 | 作用 |
|---|---|
| `ysfgateway` | 连接反射器，把 MMDVM-Host 和 YSF 网络桥接起来 |
| `mmdvmhost` | 驱动调制解调器和 C4FM 射频 |
| `oled-display` | 读取日志，驱动可选 SSD1306 |

日志：`/var/log/mmdvm/ysfgateway.log`、`/var/log/mmdvm/mmdvmhost.log`。
服务错误仍在 journald。

## 平台说明

### Raspberry Pi 5

`prepare-system.sh` 会在 `/boot/firmware/config.txt` 加入
`dtoverlay=uart0-pi5`，尝试运行时加载，并在存在时禁用 `ttyAMA0` 的串口 getty。
如果 `/dev/ttyAMA0` 仍不存在再重启。这是**已实测**路径。

### Raspberry Pi 4 / 3 / Zero 2 W（实验性）

这条路径**未做硬件测试**。`prepare-system.sh` 会加 `enable_uart=1`，优先
`dtoverlay=miniuart-bt`（蓝牙移到 mini-UART），否则回退
`dtoverlay=disable-bt`（关闭蓝牙）。它会修改 `/boot/firmware/config.txt` 或
`/boot/config.txt`、备份并提示重启。请自行确认 `/dev/ttyAMA0` 是你板子上的调制解调器
UART；桌面系统上改蓝牙可能不可接受。

### x86_64 + USB MMDVM（仅构建/CI，硬件未测）

- Pi GPIO HAT 不能直接插 x86；需要 USB 串口 MMDVM 板。
- x86 **不会**碰 `/proc/device-tree/model`、`config.txt`、`dtoverlay`、`pinctrl`
  或 serial-getty。
- 建议在 `MODEM_PORT` 写稳定的 `/dev/serial/by-id/...` 路径（auto 单设备也可）。
- 主机原生编译同一套 MMDVM-Host/YSFGateway；CI 只证明 x86_64 编译，不证明安装/射频。
- 通常没有 OLED；建议 `ENABLE_OLED=0`。外接 USB-I2C 屏幕未验证。
- 非 apt 发行版需要手动安装构建依赖。

### 其它 Linux / 特殊 UART 桥

手动。SC16IS752/SPI-UART HAT 需要自己的内核 overlay/驱动和显式 `MODEM_PORT`；本项目
不自动配置这类桥。非 systemd 系统不在支持的安装路径内。

## 会改什么

| 增加 | 不会改 |
|---|---|
| 构建工具、mosquitto/JSON 开发库、python3-pil、i2c-tools | OS 或启动镜像 |
| `mmdvm` 用户；serial/I2C 组 | 启动顺序或固件 |
| `/etc/mmdvm`、`/var/log/mmdvm`、`/var/lib/mmdvm` | 无关服务 |
| `/usr/local/bin` 二进制 | 桌面/网络配置 |
| 三个 systemd 服务 | 已有 MMDVM/Pi-Star/WPSD（preflight 拒绝） |
| 仅 Pi：`config.txt` overlay/备份；禁用 serial getty | x86 上不碰 boot 配置 |

`uninstall.sh` 会停服务、删除 add-on 文件，并从备份恢复 Pi 的 boot 配置；默认**不**
卸载共享依赖包。

## 上游补丁

`build.sh` 下载内容固定的上游源码，并应用两个补丁：

1. **`0001-ysfgateway-optional-mqtt.patch`**：上游 YSFGateway 在无本地 MQTT broker
   时会退出；补丁让它继续运行、不使用 MQTT。
2. **`0002-ysfgateway-ifstream-hosts.patch`**：上游用 `std::fstream`（读写）打开
   hosts 文件，root 只读的 `/etc/mmdvm/YSFHosts.json` 会 `EACCES`；补丁改用
   `std::ifstream`。

`upstream.lock` 记录 URL 和 SHA-256。上游变化时构建会失败；更新 lock 后必须重新
验证补丁能干净应用。

## 验证

```bash
./scripts/status.sh
tail -n 20 /var/log/mmdvm/ysfgateway.log
```

预期：

```text
Loaded 1 YSF reflectors
Linked to CN-C4FM-TG46001
```

实测配置的电台设置：

- 频率：配置的单频（示例 438.8000 MHz）
- 模式 **DN**
- 无 CTCSS/亚音
- DG-ID 0
- 不要按 WIRES-X（本方案是热点直连，不是 WIRES-X 按钮流程）

## 排障

| 现象 | 检查 |
|---|---|
| 没有串口设备 | `./scripts/preflight.sh`；单设备用 `MODEM_PORT=auto`，否则写 by-id。 |
| Pi 5 没有 `/dev/ttyAMA0` | `grep uart0-pi5 /boot/firmware/config.txt`；重启一次；确认 GPIO14/15 没被占用。 |
| Pi 4/3 UART 不通 | 确认 `enable_uart=1` 和 BT overlay；重启；确认 `/dev/ttyAMA0` 映射。实验性。 |
| MMDVM-Host 反复重启 | `journalctl -u mmdvmhost`；查 UART、HAT 供电/固件、波特率（实测 115200）。 |
| 网关启动但没 link | 查 `Startup` 是否完整名；查反射器地址/端口和网关日志。 |
| `Unable to load/parse JSON file` | 确认编进了 ifstream 补丁并安装。 |
| 日志里 MQTT connection refused | 正常；不需要 MQTT broker。 |
| OLED 不亮 | `ENABLE_OLED=auto`/`1`；`i2cdetect -y <bus>`；查 I2C 总线/地址/权限。 |
| OLED 中文方框 | 安装 DejaVu + Droid fallback 字体；预览使用混排。 |

## 卸载

```bash
sudo ./scripts/uninstall.sh            # 停/删 add-on，恢复 Pi boot 配置
sudo ./scripts/uninstall.sh --dry-run  # 只打印
sudo ./scripts/uninstall.sh --purge    # 另外删除 /var/log/mmdvm 和 mmdvm 用户
```

x86 没有 boot 配置或 GPIO overlay 需要恢复。共享依赖包默认保留。

## 安全与隐私

- 不需要 DMR ID、BrandMeister 密码、API token 或 MQTT broker。
- `config.env`、日志和构建产物都在 `.gitignore`；公开示例是 `N0CALL`。
- 反射器 hosts 是静态 JSON，请自行确认地址。
- 用 sudo 运行 `prepare-system.sh` / `install-services.sh` 前先读脚本。

## 限制

- 只有 Pi 5 配置在真实硬件上验证。
- Pi 4/3 是实验性；x86 只做到构建/CI/文档，硬件未测。
- 仅 YSF；生成配置会关闭其它 MMDVM 模式。
- 只支持一个静态反射器条目；切换需改配置并重新渲染/重启。
- Live Caller 只有呼号 + 前缀推断国家，不含姓名/城市数据库。
- 不是 Pi-Star/WPSD 替代品；已有 MMDVM 堆栈的机器请勿直接安装。
- 不刷固件，不提供预编译二进制/Release 二进制。

## 中国大陆访问 GitHub

GitHub 在中国大陆可能不稳定；`git clone`/`git push` 可能被重置或超时。可以换时间重试、
用网页 “Download ZIP”，或在网络稳定处拉取。请遵守当地法律和网络服务条款，不要使用
来源不明的代理。

## Credits / License

- [G4KLX / MMDVMHost 与 YSFClients](https://github.com/g4klx) — GPLv2
- CA6JAU MMDVM_HS 固件
- WPSD 的 Live Caller 风格作为界面灵感（未复制 WPSD 代码）
- DejaVu Sans Mono、Droid Sans Fallback、Pillow — 系统依赖，不随仓库分发
- 开发协助：Claude Code（Anthropic）——AI 辅助编写与审查，不是项目作者

本仓库脚本、模板和 OLED 代码以 **GPL-2.0** 发布；上游补丁仍受其原始 GPLv2 条款约束。
详见 [LICENSE](LICENSE)。

作者/维护者：**BI2RIP**。本项目与 Yaesu、G4KLX、CA6JAU、WPSD 无隶属关系。
