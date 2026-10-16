# SLogic-tools（产测工具）

Sipeed SLogic 系列逻辑分析仪产线产测工具，**数据驱动多产品**：SLogic16U3、SLogic32U3 及未来产品。
新增产品 = 在 `resources/products/` 加一份 TOML 档案 + 按约定放置固件与刷机资源，**无需改代码**（详见 `resources/README.md`）。

## 产线三个工位

| 工位 | 工具 | 接什么 | 做什么 |
|---|---|---|---|
| ① JTAG 烧录 | 本工具 · 「JTAG 烧录」 | Gowin 烧录器 + 空板（**不要插夹具**） | 按 JTAG IDCODE 自动识别产品 → eFuse 写入 AES 密钥（写入即锁定）→ 烧 DFU 底包 |
| ② OTA 烧应用固件 | 批量 OTA 网页工具（另行提供） | 多块 DFU 板（USB2 即可） | 并行烧 app 固件；前提是①已完成（DFU 底包 + 已加密） |
| ③ 产测 | 本工具 · 「产测」 | 被测板（运行 app 固件）+ 信号源夹具 + 一个 5G 口/Hub | 32U3：10G 链路全测 → 提示换插 5G 口 → 5G 链路再全测，两轮都过才 PASS；16U3：5G 链路测一轮 |

本工具是**一个程序**，在页头选工位（会记住上次的选择）。点 **开始** 即"布防"：每接入一块被测板，
工具自动识别产品并跑完该工位全部步骤；结论留在底部横幅上，直到下一块板接入。点 **停止** 撤防（运行中则取消）。
撤防时可在步骤列表点 ▶ 对最近识别的板单步执行。

- **产测工位逐台测**：sigrok-cli 无法在多台分析仪中选定一台（驱动不支持 `conn=`），所以主机上同时只能有一台被测分析仪；多于一台时工具会提示而不开测。
- **逐轮链路测试**（`product.toml` 的 `[usb] links`，如 32U3 = `["10G", "5G"]`）：第一轮是门槛，达不到直接 FAIL、不进入图案测试；之后每轮横幅提示把线换到对应速率的口，检测到该速率重新枚举后自动继续（固件暂无降速命令，换口是人工的）。各轮用各自的采集配置（`[[plan]]` 的 `configs` 带 `link` 标签：5G 下驱动限速，32U3 为 4ch@800 / 8ch@400 / 16ch@200 / 32ch@100）。
- 台架需要一个**只能跑 5G 的口**（如 USB 3.0 Hub）：10G 主机口插上去总是 10G。
- 结果只在界面显示（报告区可一键复制），不存档。

## 目录结构

```
├── slogicpt/            # 产测应用（唯一代码目录）
│   ├── gui.py           #   工站界面：工位选择 + 布防 + 步骤列表 + 日志/报告 + 状态横幅
│   ├── pipeline.py      #   流程引擎：工位步骤表、链路门槛、超时/人工提示
│   ├── profiles.py      #   产品档案加载/校验/资源自检
│   ├── programmer.py    #   外置 JTAG 烧录器：IDCODE 识别产品、eFuse、烧 DFU 底包
│   ├── device_watch.py  #   USB 设备检测（产品 + APP/DFU 模式 + 链路速率）
│   ├── patterns.py      #   夹具图案的期望值（唯一权威，由 RTL 推导）
│   ├── checks.py        #   判定层：逐通道 + 结构化判定（不滤波；判不了即 FAIL）
│   ├── deep_checks.py   #   内容级检查：SPI/I2C/UART 解码、突发包络
│   ├── fixture.py       #   夹具（Tang Nano 9K）位流加载（openFPGALoader 载入 SRAM）
│   ├── fpga.py          #   夹具串口：切模式、存活检查
│   ├── sigrok.py        #   sigrok-cli 采样封装（logic_channels 分组、回卷检测）
│   ├── waveform.py      #   波形拆包（任意通道数）与 PWM 验证
│   ├── flasher.py       #   DFU 烧写封装（保留供工程返修；OTA 由网页工具负责）
│   └── dfu/             #   DFU USB-SPI Flash 烧写库 + CLI
├── resources/           # 管理员资源：产品档案 / 固件 / 夹具位流 / sigrok-cli（见其 README）
├── out/                 # 运行时采样输出（自动创建，不入库）
├── build.py             # PyInstaller 单二进制打包
└── requirements.txt
```

## 快速开始

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt                     # 需 Python 3.11+
# 放置资源（sigrok-cli / 固件 / 夹具位流），见 resources/README.md
python -m slogicpt                                  # 启动 GUI，选工位后点「开始」
```

台架要点：

- **sigrok-cli 须用 SLogic 2.3.0 及以上**：旧版在 4ch@1400M 会回卷到 1600M、8ch@800M 收不到数据。
- 工位①与③的硬件分开：夹具（BL702）和 Gowin 烧录器都是 FTDI `0403:6010`，Gowin `programmer_cli` 只能按枚举序号选线，二者不能混插。
- 产品识别：JTAG 工位读 IDCODE 与各产品 `programmer.toml` 的 `idcode` 比对；产测工位按 USB PID 识别。

## 命令行

```bash
python -m slogicpt.dfu.spi_flash firmware.bin --pid 0x30F1 --addr 0x0   # DFU 烧写 CLI（工程用）
python -m slogicpt.profiles      # 档案加载与资源自检
python -m slogicpt.device_watch  # 设备插拔监视（含链路速率）
python -m slogicpt.pipeline      # 流程引擎自测（工位步骤表、链路判据）
python -m slogicpt.patterns      # 夹具图案期望值自测
python -m slogicpt.checks        # 判定层自测
```

## 打包分发

```bash
pip install pyinstaller
python build.py     # 产出 dist/slogic-pt-<platform>，与 resources/ 平级摆放分发
```
