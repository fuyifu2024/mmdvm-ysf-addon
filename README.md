# mmdvm-ysf-addon

**Add an MMDVM YSF hotspot to the Ubuntu / Raspberry Pi 5 system you already use — no dedicated image, no OS replacement, reversible.**

**在你已经在用的 Ubuntu / Pi 5 上顺手加一个 MMDVM YSF 热点——不换系统、不刷独立镜像、可以卸载回滚。**

![OLED Live Caller](docs/oled-live-caller.png)

---

## 这是什么

很多 MMDVM 热点方案要求你刷一张专用镜像（Pi-Star / WPSD），把整台机器变成“热点机”。
这个项目走另一条路：**它是一个 add-on**。适用于家里已经有一台在跑 Ubuntu 的
Raspberry Pi 5（服务器、桌面机、电台电脑都行），想顺手多跑一个 YSF 热点的情况。

它会在现有系统里增加：

- 一个 `mmdvm` 系统用户
- 三个 systemd 服务：`ysfgateway`、`mmdvmhost`、`oled-display`
- `/etc/mmdvm`、`/var/log/mmdvm`、`/var/lib/mmdvm`
- `/usr/local/bin/{MMDVM-Host,YSFGateway,oled_display.py}`
- `/boot/firmware/config.txt` 中的 `dtoverlay=uart0-pi5`
- 一组构建/运行依赖（见下文）

它**不会**替换 OS、改启动顺序、刷 HAT 固件、重装桌面，或接管你已有的其它服务。
所有改动集中在上面这些 add-on 自己的文件和三个服务里。

> 老实说：它**不是零改动**。启用 GPIO14/15 的 UART、安装依赖、增加三个服务都是
> 真实改动；所以项目提供了 preflight 冲突检查和 uninstall 回滚。README 的
> “会改什么”一节把每项都列清楚。

## 已验证环境

- Raspberry Pi 5 + **Ubuntu 26.04.1 LTS (arm64)**
- MMDVM_HS_Hat，固件 `MMDVM_HS_Hat-v1.5.2 20201108 14.7456MHz ADF7021 FW by CA6JAU`
- 板载 SSD1306 OLED（Pi I2C-1，地址 `0x3C`）
- YSF 反射器 `46001 / CN-C4FM-TG46001`（`222.128.4.82:42000`）
- 作者：BI2RIP（FT-5D / FTM-510D）

作者现场实测过 144.3000 MHz 的 2m 配置，但**公开示例默认是 438.8000 MHz**。
144.300 属于 2m 弱信号/SSB 呼叫区域，**不建议**作为热点默认频点；详见下面的频率说明。

## 前提

- Pi 5 上已经能正常跑 Ubuntu，且你能 SSH 或直接在桌面操作。
- GPIO14/15 没有被其它 HAT/服务占用；I2C-1 上不要有第二个 0x3C 设备。
- HAT 的 STM32 固件已经刷好（本项目**不**负责刷固件）。
- 机器上**没有**已经运行的 MMDVM / Pi-Star / WPSD：preflight 会检查并拒绝冲突。
- 你的业余无线电呼号、合法频点，以及（如适用）当地频率协调。
- Python 3 + Pillow（`python3-pil`，prepare 会安装）。

## 快速开始

```bash
git clone https://github.com/fuyifu2024/mmdvm-ysf-addon.git
cd mmdvm-ysf-addon

cp config.example.env config.env
$EDITOR config.env          # 至少改 CALLSIGN 和 FREQUENCY

./scripts/preflight.sh      # 只读检查：Pi 5、UART、I2C、冲突
sudo ./scripts/prepare-system.sh   # UART overlay、依赖、dialout/i2c
python3 scripts/render_configs.py # 生成 build/etc 配置
./scripts/build.sh          # 下载固定上游 + 打补丁 + 编译
sudo ./scripts/install-services.sh # 安装并启动三个服务
./scripts/status.sh         # 查看服务、链路、日志、硬件
```

也可以：

```bash
make preflight
make config
sudo make prep
make build
sudo make install
make status
```

如果 `prepare-system.sh` 结束时提示 `/dev/ttyAMA0` 还不存在，重启一次再继续；
`config.txt` 里的 overlay 会在下次启动时生效。`prepare-system.sh` 不会重启现有服务；
`install-services.sh` 只重启本 add-on 的三个服务。

## 配置

`config.example.env` 是公开示例（N0CALL / 438.8000），复制成 `config.env` 后编辑。
`config.env` 已被 `.gitignore` 排除，不会提交。

| 键 | 说明 |
|---|---|
| `CALLSIGN` | 你的呼号，大写字母/数字 3–10 位。示例 `N0CALL` 只是占位，发射前必须改。 |
| `SUFFIX` | YSF 节点后缀，通常 `ND`。 |
| `FREQUENCY` | 单频热点频率（MHz），RX/TX 相同。公开默认 `438.8000`。 |
| `REFLECTOR_ID` | YSF 反射器编号，例如 `46001`。 |
| `REFLECTOR_COUNTRY` | 反射器国家前缀，例如 `CN`。 |
| `REFLECTOR_NAME` | 反射器短名，例如 `C4FM-TG46001`（**不要**带国家前缀）。 |
| `REFLECTOR_DESC` | 描述文本。 |
| `REFLECTOR_STARTUP` | 完整名，必须等于 `COUNTRY-NAME`，例如 `CN-C4FM-TG46001`。 |
| `REFLECTOR_HOST` | 反射器地址（IPv4 或主机名）。 |
| `REFLECTOR_PORT` | 反射器 UDP 端口，46001 是 `42000`。 |
| `HOTSPOT_NAME` / `HOTSPOT_DESC` | YSFGateway Info 里的名称/描述。 |

### 为什么 `Startup` 必须写完整名

当前 YSFGateway 源码用 `findByName()` 匹配 `Startup`，不是按数字 ID。JSON hosts 里
的条目由 `COUNTRY + "-" + NAME` 组成完整名，所以：

```ini
REFLECTOR_COUNTRY=CN
REFLECTOR_NAME=C4FM-TG46001
REFLECTOR_STARTUP=CN-C4FM-TG46001
```

三者必须一致。`render_configs.py` 会校验，写错会直接报错，避免“网关启动了但没
link 上”的坑。

### 频率说明

- 公开示例使用 **438.8000 MHz**，这是常见的 UHF 单频热点默认值之一；仍需确认你
  当地没有同频中继/热点，并遵守所在国家的频段规划。
- 作者现场用过 **144.3000 MHz** 做 2m 实验，HAT 与 2m 天线在该频率可收发；但
  144.300 是国际公认的 2m 弱信号/SSB 呼叫区域，**不适合作为数字热点默认频点**。
  本仓库只在 README 记录这一实测，不把它放进示例配置。请优先使用 438.8000 或
  你当地协调过的频点。
- 首次发射建议接 50Ω 假负载或确认驻波正常；不要用 430 MHz 天线在 144 MHz 发射。

## OLED Live Caller

OLED 在 Pi 的 I2C-1 `0x3C`，由 Pi 侧直接驱动，不是 STM32 固件驱动。

```text
▓ LIVE CALLER        <your call> ▓
        <speaker call>
      <country> · NET/RF
● <reflector> <frequency>   <clock>
```

- 主位是当前/最近一次发射者的呼号（大号字）。
- 右上角固定显示你自己的呼号（来自 `OWN_CALL`）。
- 地区按呼号前缀离线推断；YSF 帧不带姓名/城市，所以没有 WPSD DMR 库里的姓名/城市。
- `NET` = 来自反射器网络，`RF` = 本机射频；RF 会显示 BER。
- 空闲 90 秒后显示 `STANDBY`；链路状态/时间一直在底部。
- 生成 PNG 预览（不访问 I2C）：

```bash
python3 oled/oled_display.py --preview /tmp/oled.png \
  --own-call N0CALL --reflector 46001 --frequency 438.800 --demo-call JA1ABC
```

OLED 服务使用 `/etc/mmdvm/oled.env` 里的 `OWN_CALL`、`REFLECTOR_ID`、
`FREQUENCY_LABEL`。如果机器没有 0x3C 屏幕，`install-services.sh` 会跳过 OLED 服务，
MMDVM 服务仍可正常运行。

## 工作原理

```text
FT-5D / FTM-510D  (C4FM, DN)
        │  144/430 MHz
        ▼
MMDVM_HS_Hat (STM32) ── GPIO14/15 UART ── /dev/ttyAMA0
        │
        ▼
   MMDVM-Host  ── 127.0.0.1:3200/4200 ──  YSFGateway
                                            │ UDP 42000
                                            ▼
                                 YSF reflector (e.g. 46001)

SSD1306 OLED (I2C-1 0x3C) ◄── oled_display.py ◄── logs in /var/log/mmdvm
```

三个 systemd 服务：

| 服务 | 作用 |
|---|---|
| `ysfgateway` | 连接反射器，把 MMDVM-Host 和 YSF 网络桥接起来 |
| `mmdvmhost` | 驱动 HAT 的 STM32，收发 C4FM |
| `oled-display` | 从日志读取链路/发射者，驱动 SSD1306 |

日志：`/var/log/mmdvm/ysfgateway.log`、`/var/log/mmdvm/mmdvmhost.log`。
服务错误仍在 journald；常用：

```bash
./scripts/status.sh
tail -f /var/log/mmdvm/ysfgateway.log
tail -f /var/log/mmdvm/mmdvmhost.log
journalctl -u oled-display -n 30
```

## 上游补丁

`build.sh` 会下载固定内容的上游源码，并应用两个补丁：

1. **`0001-ysfgateway-optional-mqtt.patch`**：YSFGateway 上游在没有本地 MQTT
   broker 时会直接退出；补丁改为“连不上 MQTT 就继续，不使用 MQTT”。
2. **`0002-ysfgateway-ifstream-hosts.patch`**：上游用 `std::fstream`（默认读写）
   打开 hosts 文件，root 只读的 `/etc/mmdvm/YSFHosts.json` 会 `EACCES`；补丁改为
   `std::ifstream`。

`upstream.lock` 记录下载 URL 和 SHA-256。上游变化时构建会失败，提示更新 lock；
更新后必须重新验证补丁能干净应用。

## 验证与通联

1. 看服务与链路：

```bash
./scripts/status.sh
tail -n 20 /var/log/mmdvm/ysfgateway.log
```

预期看到：

```text
Loaded 1 YSF reflectors
Linked to CN-C4FM-TG46001
```

2. 电台设置：

- 频率 = `FREQUENCY`（默认 438.8000 MHz）
- 模式 **DN**（C4FM digital）
- **无** CTCSS/亚音
- DG-ID 0
- 不要按 WIRES-X（本方案是热点直连，不是 WIRES-X 按钮流程）
- 先用假负载/合适天线，短按 PTT 测试

3. 对方呼号/链路会出现在 MMDVM-Host 日志和 OLED 上。

## 排障

| 现象 | 检查 |
|---|---|
| 没有 `/dev/ttyAMA0` | `grep uart0-pi5 /boot/firmware/config.txt`；重启一次；确认没有其它服务占用 GPIO14/15。 |
| MMDVM-Host 反复重启 | `journalctl -u mmdvmhost`；确认 UART 存在、HAT 插好、固件在运行（OLED/板子供电）。 |
| 网关启动但没有 link | 看 `/var/log/mmdvm/ysfgateway.log` 是否有 `Load`/`Linked`；确认 `Startup` 是完整名；确认反射器地址/端口。 |
| `Unable to load/parse JSON file` | 确认安装了带 ifstream 补丁的二进制；`build.sh` 是否成功。 |
| 日志里有 MQTT connection refused | 正常：本方案不需要 MQTT broker；两个程序都设计为继续运行。 |
| OLED 不亮 | `i2cdetect -y 1` 是否有 0x3C；确认 `oled-display.service`；检查 `/dev/i2c-1` 权限。 |
| 中文显示方框 | 需要 DejaVu + DroidSansFallback 字体；README 预览使用混合字体渲染。 |
| 服务起来但没射频 | HAT 频段/天线/驻波；先用假负载；确认 `FREQUENCY` 在 HAT 支持的波段内。 |

## 卸载

```bash
sudo ./scripts/uninstall.sh            # 停服务，删 add-on 文件，恢复 config.txt
sudo ./scripts/uninstall.sh --dry-run  # 只打印，不改
sudo ./scripts/uninstall.sh --purge    # 另外删除 /var/log/mmdvm 和 mmdvm 用户
```

默认**不会**卸载共享依赖包，也不会碰其它服务。`config.txt` 会从
`config.txt.bak-mmdvm-addon` 恢复；如果备份丢失，脚本只会提示你手动移除
`dtoverlay=uart0-pi5` 行，不会擅自改其它设置。

## 安全与隐私

- 不需要 DMR ID、BrandMeister 密码或任何 API token。
- 不安装/运行 MQTT broker；两个程序的 MQTT 行为已处理。
- `config.env`、日志、构建产物都在 `.gitignore` 里；公开示例用 `N0CALL`。
- 反射器 hosts 是静态 JSON，不自动下载；请自己确认地址有效。
- 运行 `sudo ./scripts/prepare-system.sh` / `install-services.sh` 前，先读一遍脚本。

## 限制

- 仅测试 Pi 5 + Ubuntu；Pi 4 / 其它发行版需要不同的 UART overlay，未验证。
- 仅 YSF；D-Star/DMR/P25/NXDN/POCSAG/FM 在配置中全部关闭。
- 不刷 HAT 固件；需要先自备可工作的 MMDVM_HS 固件。
- 当前只支持一个静态 YSF 反射器条目；切换反射器请改 `config.env` 后重渲染/重启。
- Live Caller 只有呼号 + 前缀推断国家，不含姓名/城市（YSF 帧没有这些字段）。
- 不是 Pi-Star/WPSD 替代品；已有 MMDVM/Pi-Star/WPSD 的机器请勿直接安装。

## 中国大陆访问 GitHub

GitHub 在中国大陆的访问可能不稳定；`git clone`/`git push` 可能被 TLS 重置或超时。
如果遇到：

- 重试或换时间段；
- 用 GitHub 网页的 “Download ZIP”；
- 或在能稳定访问的环境拉取后再拷贝；

请遵守当地法律和网络服务条款，不要使用来源不明的代理服务。

## Credits / License

- [G4KLX / MMDVMHost and YSFClients](https://github.com/g4klx) — GPLv2
- CA6JAU MMDVM_HS firmware
- WPSD 的 Live Caller 风格作为界面灵感（未复制其代码）
- DejaVu Sans Mono、Droid Sans Fallback、Pillow — 作为系统依赖，不随仓库分发

本仓库的脚本、配置模板和 OLED 代码以 **GPL-2.0** 发布；上游补丁同时受其原始
GPLv2 条款约束。详见 [LICENSE](LICENSE)。

Author / maintainer: **BI2RIP**. 本项目与 Yaesu、G4KLX、CA6JAU、WPSD 无隶属关系。
