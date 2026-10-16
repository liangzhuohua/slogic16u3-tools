# -*- coding: utf-8 -*-
"""英文 key -> 中文 对照表（由 slogicpt.i18n.t 使用）。

只放用户可见的界面/日志/报告/异常文案。英文是源码里的 key，此处给中文。
新增/修改界面英文时，务必在此同步中文，否则运行到 zh 会回退成英文（可见）。
分节仅为便于维护，运行时是一个扁平 dict。占位符 {name} 在中英值中保持一致，
以便 str.format() 在两种语言下都能填值。
"""

ZH: dict[str, str] = {
    # ---- gui.py ----------------------------------------------------------
    # status glyph labels (STATUS_STYLE)
    "Pending": "待执行",
    "Running…": "执行中…",
    "Waiting": "等待操作",
    "Pass": "通过",
    "Fail": "失败",
    "Skip": "跳过",
    # step row / logs
    "Single step: {label}": "单步执行：{label}",
    # startup
    "No product profiles": "无产品档案",
    "No usable product profiles under resources/products/; "
    "see resources/README.md to add them.":
        "resources/products/ 下没有可用的产品档案，请参照 resources/README.md 放置。",
    # header
    "Product:": "产品:",
    "Stop": "停止",
    # sequence panel
    # detail panes (report on top, live log on bottom) + ⚙ capture-params popup
    "Run Log": "运行日志",
    "Clear": "清空",
    "Channels:": "通道数:",
    "Sample Rate:": "采样率:",
    "Samples:": "采样点数:",
    "Voltage Threshold (V):": "电压阈值(V):",
    "Custom Capture Verify (engineering)": "自定义采样验证（工程调试）",
    "Per-channel expected values (double-click to edit; "
    "sequence steps use profile values):":
        "每通道期望值（双击修改；序列步骤使用产品档案值）:",
    "Test Params": "测试参数",
    "Session report (paste directly into feedback):": "本次会话报告（可直接粘贴反馈）:",
    "Copy Report": "复制报告",
    # resource-path overrides + one-time environment setup (under Auxiliary)
    "Environment Setup (one-time) · {name}": "环境准备（一次性）· {name}",
    "Deploy": "部署",
    "Remove": "移除",
    # banner
    "Standby": "待机",
    # problem chip / dialog
    "{n} errors": "{n} 错误",
    "{n} warnings": "{n} 警告",
    "Click for resource self-check details": "点击查看资源自检详情",
    "Resource Self-check": "资源自检",
    "Startup self-check found the following issues (missing items disable the "
    "related function; add resources and restart to take effect):":
        "启动自检发现以下问题（缺失项对应功能已禁用，补齐资源后重启生效）：",
    "No issues": "无问题",
    # product change placeholders
    "Profile defines no steps": "档案未定义任何步骤",
    # aux buttons
    # eFuse badge
    # device status
    "libusb backend not found (libusb-1.0.dll): cannot enumerate USB devices, "
    "DFU/APP detection disabled — see resources/README.md":
        "未找到 libusb 后端（libusb-1.0.dll）：无法枚举 USB 设备，"
        "DFU/APP 检测失效——见 resources/README.md",
    "Device: {dev}": "设备: {dev}",
    # enablement tooltips
    "Missing: {items}": "缺少: {items}",
    # fixture plug/unplug guidance (scan button & full-test setup)
    "Requires Tang Nano 9K plugged in": "需插着 Tang Nano 9K",
    "Requires Tang Nano 9K unplugged": "需拔掉 Tang Nano 9K",
    # switch button
    # actions / logs
    "exception: {e}": "异常: {e}",
    # env buttons
    "Redeploy": "重新部署",
    "Rule exists; rewrite to update": "规则已存在，可重新写入以更新",
    "Remove the deployed rule": "移除已部署的规则",
    "Not deployed; nothing to remove": "尚未部署，无需移除",
    # mode switch / reflash / sampling
    "Cannot switch": "无法切换",
    "Parameter error": "参数错误",
    "Custom capture: {ch}ch@{rate}": "自定义采样: {ch}ch@{rate}",
    "Stop requested…": "已请求停止…",
    # resource rows / file dialogs
    "Copied": "已复制",
    # language switch guard
    "Cannot switch language while a test is running.": "测试运行中无法切换语言。",
    # slots
    "Current step: {label}": "当前步骤: {label}",
    "Running — {label}": "运行中 — {label}",
    "Manual mode switch required": "需要手动切换模式",
    "; failed steps: {items}": "；失败步骤: {items}",
    # report
    "SLogic Production Test Report": "SLogic 产测报告",
    "Time: {ts}": "时间: {ts}",
    "Product: {name} ({id})": "产品: {name} ({id})",
    "Product: -": "产品: -",
    "Device: {devs}": "设备: {devs}",
    "not detected": "未检测到",
    "Overall: {result}  (elapsed {s:.1f}s)": "总结果: {result}  (耗时 {s:.1f}s)",
    "Step Status": "步骤状态",
    "Details": "详情",
    "To report an issue, copy this report and attach the relevant run-log excerpts.":
        "如需反馈问题，请复制本报告并附上运行日志相关片段。",

    # ---- pipeline.py -----------------------------------------------------
    "Blank-flash · eFuse key write-lock": "烧空板 · eFuse 密钥写锁",
    "Blank-flash · write DFU image": "烧空板 · 烧写 DFU 镜像",
    "Wait for DFU device": "等待 DFU 设备",
    "DFU flash app firmware": "DFU 烧写应用固件",
    "Switch to APP mode": "切换到 APP 模式",
    "Wait for APP device": "等待 APP 设备",
    "Switch to DFU mode": "切换到 DFU 模式",
    "Capture {ch}ch@{rate}": "采样验证 {ch}ch@{rate}",
    "Custom capture {ch}ch@{rate}": "自定义采样 {ch}ch@{rate}",
    "Unknown step: {step_id}": "未知步骤: {step_id}",
    "Cancelled": "已取消",
    "Step \"{label}\" failed": "步骤「{label}」失败",
    "Flow aborted: {e}": "流程中止: {e}",
    "Flow exception:": "流程异常:",
    "Flow exception, see log for details": "流程异常，详见日志",
    "eFuse already locked (key written), skipping write-lock":
        "eFuse 已锁定（密钥已写入），跳过写锁",
    "eFuse write-lock": "eFuse 写锁",
    " (already locked)": "（已锁定）",
    "Cannot confirm eFuse state (external programmer not connected / cable fault?), aborting":
        "无法确认 eFuse 状态（外置烧录器未连接/线缆异常？），中止",
    " (state unknown)": "（状态未知）",
    "eFuse not locked: writing AES key and locking (prerequisite for encrypted "
    "DFU boot, irreversible)...":
        "eFuse 未锁：写入 AES 密钥并锁定（加密 DFU 启动前提，不可逆）…",
    " (write failed)": "（写入失败）",
    " (readback confirmed locked)": "（回读确认已锁定）",
    " (readback did not confirm locked)": "（回读未确认锁定）",
    "eFuse readback did not confirm locked, judged as failed": "回读 eFuse 未确认锁定，判定失败",
    "Blank-flash DFU image": "烧空板 DFU 镜像",
    "Waiting for {what} device ({vid}:{pid})...": "等待 {what} 设备 ({vid}:{pid})...",
    "{what} device ready": "{what} 设备已就绪",
    " (after manual intervention)": "（人工介入后）",
    "Waiting for {what} device timed out -- please replug":
        "等待 {what} 设备超时，请重新插拔/上电设备…",
    "APP SerialNumber: {sn} (for version identification/distinction)":
        "APP SerialNumber: {sn}（用于版本识别/区分）",
    "APP device provided no SerialNumber descriptor": "APP 设备未提供 SerialNumber 描述符",
    "dfu_pid not configured, cannot switch to APP": "dfu_pid 未配置，无法切换到 APP",
    "No APP device detected, skipping auto-switch, waiting for DFU directly":
        "未检测到 APP 设备，跳过自动切换，直接等待 DFU",
    "Sending RECONFIG to switch to {mode} mode ({vid}:{pid})...":
        "发送 RECONFIG 切换到 {mode} 模式 （{vid}:{pid}）...",
    "RECONFIG switch failed: {e}, trying fallback...": "RECONFIG 切换失败: {e}，尝试保底方案…",
    "External programmer switch failed, falling back to manual...":
        "外置烧录器切换失败，转人工…",
    "Please press the onboard MODE button to switch {name} to {mode} mode; it "
    "will continue automatically once the target-mode device is detected":
        "请按板载 MODE 按键，将 {name} 切换到 {mode} 模式；检测到目标模式设备后自动继续",
    "dfu_pid not configured": "dfu_pid 未配置",
    "Firmware or dfu_pid not configured, cannot flash": "固件或 dfu_pid 未配置，无法烧写",
    "DFU flash": "DFU 烧写",
    "== Capture {label} ({samples} samples) ==": "== 采样 {label} ({samples} samples) ==",
    "Multiple SLogic devices detected online at once; the current sigrok driver "
    "does not yet support selecting a device, please keep only the device under "
    "test and retry:":
        "检测到多台 SLogic 设备同时在线，当前 sigrok 驱动暂不支持指定设备，请只保留被测设备后重试:",
    "multiple-device ambiguity": "多设备歧义",
    "Capture failed: {e}": "采样失败: {e}",
    "Capture done: {n} samples, {sec}s -> {file}": "采样完成: {n} samples, {sec}s -> {file}",
    "{n}/{total} channels passed": "{n}/{total} 通道通过",

    # ---- fixture/protocol steps (pipeline._run_mode_config) --------------
    "Applied the profile's signal_source.channel_order permutation to this capture":
        "已按档案的 signal_source.channel_order 重排本次采样",
    "Signal generator · all {n} patterns": "信号发生器 · 全部 {n} 个模式",
    "Unpack failed: {e}": "解包失败: {e}",
    "Capture failed: {e}": "采样失败: {e}",
    "Signal generator unavailable: {e}": "信号发生器不可用: {e}",
    "no signal generator": "无信号发生器",
    "Mode {m} not confirmed: {e}": "模式 {m} 未确认: {e}",
    "mode not confirmed": "模式未确认",
    "M{mode} is not ready: {note}": "M{mode} 未就绪: {note}",
    "not ready": "未就绪",
    "no serial port after flashing": "烧录后没有串口",
    "Signal generator on {p}, alive, mode 0": "信号发生器在 {p}，存活，模式 0",
    "Loading fixture: {name} (run {run})": "烧录夹具: {name} (run {run})",
    "Fixture flash FAILED; the capture steps will report SKIP":
        "夹具烧录失败；后续采样步骤将报 SKIP",
    "no firmware declared": "未声明固件",
    "== signal generator: {p} patterns pass/skip, {f} fail ==":
        "== 信号发生器: {p} 个模式通过/跳过，{f} 个失败 ==",
    "Capture attempt {a}/{n} failed ({e}); retrying":
        "采样第 {a}/{n} 次失败（{e}），重试中",
    "Capture failed: {e}": "采样失败: {e}",

    # The end-of-flow summary that names the wires to go and look at.
    "---- failing channels ({n}) ----": "---- 不通过的通道（{n} 处）----",
    "got": "实测",
    "want": "应为",
    "ok in group": "组内正常",
    # ---- 夹具插拔提示（夹具和被测板烧录器共用 FTDI 0403:6010）----
    "Please UNPLUG the signal-generator fixture (Tang Nano 9K) and wait -- "
    "it shares the FTDI ID with the board's programmer, so this step cannot "
    "address the board while the fixture is connected":
        "请【拔掉】信号发生器夹具（Tang Nano 9K）的 USB，然后等待 —— "
        "它和被测板的烧录器共用同一个 FTDI 标识，夹具插着时这一步寻址不到被测板",
    "Please PLUG IN the signal-generator fixture (Tang Nano 9K) and wait -- "
    "the flow continues by itself":
        "请【插上】信号发生器夹具（Tang Nano 9K）的 USB，然后等待 —— 流程会自动继续",
    "Fixture detected ({p}), continuing": "已检测到夹具（{p}），继续",
    "Fixture unplugged, continuing": "夹具已拔下，继续",
    "still waiting ({s}s): {m}": "仍在等待（{s} 秒）：{m}",

    # ---- programmer.py ---------------------------------------------------
    "Launch failed: {e}": "启动失败: {e}",
    "cancelled": "已取消",
    "timed out ({secs}s)": "超时 ({secs}s)",
    "Programmer CLI not found (programmer.cli undeclared and no default AppImage)":
        "未找到烧录器 CLI（programmer.cli 未声明且无默认 AppImage）",
    "Programmer CLI not found": "未找到烧录器 CLI",
    "Trying cable-index {cable} (device={dev}) to read the IDCODE...":
        "尝试 cable-index {cable}（device={dev}）读器件码…",
    "Result: external programmer connected, device={dev}, cable-index={cable}, eFuse={efuse}":
        "结果：外置烧录器已连接，device={dev}，cable-index={cable}，eFuse={efuse}",
    "Result: no device read on any cable -- external programmer not connected or unpowered":
        "结果：未在任何 cable 上读到器件——外置烧录器未连接或未上电",
    "No device read on any cable (external programmer not connected?)":
        "未在任何 cable 上读到器件（外置烧录器未连接？）",
    "No cable-index provided, probing first...": "未提供 cable-index，先探测…",
    "programmer.toml declares no [programmer.flash]; cannot flash":
        "programmer.toml 未声明 [programmer.flash]，无法烧录",
    "DFU image missing: {img}": "DFU 镜像缺失: {img}",
    "Blank-flash (custom command, watchdog {secs}s): ": "烧空板（自定义命令，看门狗 {secs}s）: ",
    "flash complete": "烧录完成",
    "flash failed": "烧录失败",
    "Programmer CLI not found; cannot flash": "未找到烧录器 CLI，无法烧录",
    "No usable cable found; external programmer not connected?":
        "未找到可用 cable，外置烧录器未连接？",
    "Blank-flash: device={device} cable-index={cable} run={run} --fsFile={fsfile} "
    "spiaddr={spiaddr} (watchdog {secs}s)":
        "烧空板：device={device} cable-index={cable} run={run} --fsFile={fsfile} "
        "spiaddr={spiaddr} (看门狗 {secs}s)",
    "state: {state} (cable-index={cable})": "状态: {state}（cable-index={cable}）",
    "Switching via external programmer ({direction}): device={device} cable-index={cable}":
        "经外置烧录器切换（{direction}）：device={device} cable-index={cable}",
    "switch command complete": "切换命令完成",
    "switch command failed": "切换命令失败",
    "Programmer CLI not found; cannot write-lock": "未找到烧录器 CLI，无法写锁",
    "programmer.toml declares no [programmer.efuse]; cannot write-lock":
        "programmer.toml 未声明 [programmer.efuse]，无法写锁",
    "Key file missing: {key}": "密钥文件缺失: {key}",
    "Writing and locking AES key (irreversible): device={device} cable-index={cable} keyFile={keyfile}":
        "写入并锁定 AES 密钥（不可逆）：device={device} cable-index={cable} keyFile={keyfile}",
    "write-lock complete (key written and locked)": "写锁完成（密钥已写入并锁定）",
    "write-lock failed": "写锁失败",

    # ---- profiles.py (self-check Problem messages) -----------------------
    "programmer.toml missing: {f}": "programmer.toml 缺失: {f}",
    "programmer.toml parse failed: {f}: {e}": "programmer.toml 解析失败: {f}: {e}",
    "programmer.toml missing [programmer] section: {f}": "programmer.toml 缺 [programmer] 段: {f}",
    "programmer.toml [programmer] missing device: {f}": "programmer.toml [programmer] 缺 device: {f}",
    "cable_candidates is empty": "cable_candidates 为空",
    "[programmer.switch] must declare at least dfu2app or app2dfu":
        "[programmer.switch] 需至少声明 dfu2app 或 app2dfu",
    "programmer.toml [programmer] invalid: {e}": "programmer.toml [programmer] 无效: {e}",
    "profile parse failed: {e}": "档案解析失败: {e}",
    "schema_version should be {expected}, actual {actual!r}":
        "schema_version 应为 {expected}, 实际 {actual!r}",
    "product.id ({pid}) does not match directory name ({dirname})":
        "product.id ({pid}) 与目录名 ({dirname}) 不一致",
    "usb.dfu_pid not configured; DFU and one-click flow will be disabled":
        "usb.dfu_pid 未配置，DFU 与一键流程将禁用",
    "channel_options {opts} exceed num_channels={n}":
        "channel_options {opts} 超出 num_channels={n}",
    "default_channels={dc} not in channel_options": "default_channels={dc} 不在 channel_options 中",
    "default_samplerate={sr} is invalid for {dc}ch (not in the list or exceeds bandwidth)":
        "default_samplerate={sr} 对 {dc}ch 不合法（不在档位或超带宽）",
    "capture.tests[{i}].channels={ch} exceeds num_channels":
        "capture.tests[{i}].channels={ch} 超出 num_channels",
    "capture.tests[{i}].samplerate={sr} not in samplerates list":
        "capture.tests[{i}].samplerate={sr} 不在 samplerates 列表中",
    "capture.tests is empty; one-click flow has no test points":
        "capture.tests 为空，一键流程无测试点",
    "test point {ch}ch@{rate} exceeds bandwidth {bw}MB/s":
        "测试点 {ch}ch@{rate} 超出带宽 {bw}MB/s",
    "missing required field": "缺少必填字段",
    "invalid field": "字段无效",
    "product profile directory does not exist: {d}": "产品档案目录不存在: {d}",
    "product profile directory is empty: {d}; please place <product_id>/product.toml":
        "产品档案目录为空: {d}，请放置 <product_id>/product.toml",
    "app VID/PID {vidpid} conflicts with product {other}":
        "app VID/PID {vidpid} 与产品 {other} 冲突",
    "app VID/PID {vidpid} conflicts with dfu_pid of product {other}":
        "app VID/PID {vidpid} 与产品 {other} 的 dfu_pid 冲突",
    "sigrok-cli binary not found; place it in {d} with the platform-specific name "
    "(see resources/README.md); capture disabled":
        "未找到 sigrok-cli 二进制，请按平台命名放入 {d}（见 resources/README.md），采样功能禁用",
    "firmware.app not configured; DFU and one-click flow disabled":
        "firmware.app 未配置，DFU 与一键流程禁用",
    "application firmware missing: {path}; DFU and one-click flow disabled":
        "应用固件缺失: {path}，DFU 与一键流程禁用",
    "programmer CLI not found (programmer.cli not declared and no Gowin AppImage "
    "in resources/bin); blank-flash/eFuse disabled":
        "未找到烧录器 CLI（programmer.cli 未声明且 resources/bin 无 Gowin AppImage），烧空板/eFuse 功能禁用",
    "blank-flash DFU image missing: {path}; blank-flash disabled":
        "烧空板 DFU 镜像缺失: {path}，烧空板禁用",
    "eFuse key file missing: {path}; the eFuse write-lock prerequisite of "
    "blank-flash will fail":
        "eFuse 密钥文件缺失: {path}，烧空板的 eFuse 写锁前置步骤将失败",
    "switch {d} references a missing file: {path}; this direction falls back to a "
    "manual dialog":
        "切换 {d} 引用的文件缺失: {path}，该方向回退弹窗人工",

    # ---- sigrok.py / flasher.py / mode_switch.py -------------------------
    "Capture cancelled": "采样已取消",
    "Capture timed out ({s:.0f}s)": "采样超时 ({s:.0f}s)",
    "Device does not support samplerate {rate} (driver wrapped to {wrapped}). "
    "Please fix the product profile capture.samplerates":
        "设备不支持采样率 {rate}（驱动回卷到 {wrapped}）。请修正产品档案 capture.samplerates",
    "sigrok-cli exit code {code}": "sigrok-cli 退出码 {code}",
    "No capture data file produced: {path}": "未产生采样数据文件: {path}",
    "Capture data is less than one full sample": "采样数据不足一个样本",
    "Firmware file not found: {path}": "固件文件不存在: {path}",
    "Firmware: {path} ({size} bytes) -> {vid:#06x}:{pid:#06x} @ {addr:#x}":
        "固件: {path} ({size} bytes) -> {vid:#06x}:{pid:#06x} @ {addr:#x}",
    "USB transfer failed ({err}), resetting device and retrying once…":
        "USB 传输失败（{err}），复位设备后重试一次…",
    "DFU flash failed: {err}": "DFU 烧写失败: {err}",
    "Device not found after reset": "复位后未找到设备",
    "Device {vid:#06x}:{pid:#06x} not found, cannot send RECONFIG":
        "未找到设备 {vid:#06x}:{pid:#06x}，无法发送 RECONFIG",
    "RECONFIG sent to {vid:#06x}:{pid:#06x}": "RECONFIG 已发送至 {vid:#06x}:{pid:#06x}",
    "RECONFIG triggered (device offline, control transfer returned {err}, expected)":
        "RECONFIG 已触发（设备离线，控制传输返回 {err}，属预期）",
    # flasher.py: RECONFIG-based recovery from a hung DFU device
    "Still no response after reset ({err}); rebooting the device (RECONFIG) "
    "and retrying…":
        "复位后仍无响应（{err}）；用 RECONFIG 重启设备后再试一次…",
    "DFU flash failed: device unresponsive ({err}); power-cycle the device "
    "(unplug and replug its USB) and retry":
        "DFU 烧写失败: 设备无响应（{err}）；请给设备断电重启"
        "（拔掉 USB 再插回）后重试",
    "RECONFIG to {vid:#06x}:{pid:#06x} reported: {err}":
        "向 {vid:#06x}:{pid:#06x} 发送 RECONFIG 返回: {err}",
    "Rebooting the device with RECONFIG ({vid:#06x}:{pid:#06x})…":
        "正在用 RECONFIG 重启设备（{vid:#06x}:{pid:#06x}）…",
    "Device did not re-enumerate after RECONFIG":
        "RECONFIG 后设备没有重新枚举",
    "Device rebooted straight back into DFU mode":
        "设备重启后直接回到了 DFU 模式",
    "Device rebooted into APP mode; sending RECONFIG once more to return to DFU":
        "设备重启后落到了 APP 模式；再发一次 RECONFIG 切回 DFU",
    "Device is back in DFU mode after the RECONFIG reboot":
        "RECONFIG 重启后设备已回到 DFU 模式",
    "Device did not return to DFU mode after RECONFIG":
        "RECONFIG 后设备没有回到 DFU 模式",

    # ---- env_setup.py (title/hint only; deploy log stays English) --------
    "Linux device permissions (udev)": "Linux 设备权限 (udev)",
    "Windows driver (WinUSB)": "Windows 驱动 (WinUSB)",
    "Install a udev rule for SLogic (359F) and the FTDI cable (0403:6010) for "
    "root-free access; deploying pops up an authorization dialog, then replug "
    "the device to take effect.":
        "为 SLogic(359F) 与 FTDI 线缆(0403:6010) 安装 udev 规则，免 root 访问；"
        "部署会弹出授权框，之后重新插拔设备生效。",
    "Bind the FTDI A-channel (0403:6010 interface 0) to WinUSB, required by "
    "openFPGALoader's high-speed flashing; deploying pops up a UAC authorization dialog.":
        "将 FTDI A 通道(0403:6010 接口0)绑定到 WinUSB，openFPGALoader 高速烧录所需；"
        "部署会弹出 UAC 授权框。",

    # ---- dfu/ (exceptions surfaced via flasher) --------------------------
    "Flash communication error: SR1 stuck reading 0xFF (bus hung)":
        "Flash 通信异常：SR1 恒读 0xFF（总线挂起）",
    "Flash write wait timed out: WIP did not clear in time":
        "Flash 写等待超时：WIP 未在预期内清零",
    "Start address 0x{addr:06X} is not 64KB-aligned": "起始地址 0x{addr:06X} 未按 64KB 对齐",
    "Firmware is empty": "固件为空",
    "SPI flash reset failed": "SPI flash reset 失败",
    "Flash not responding (ID={dev_id}); check the DFU connection and retry":
        "Flash 无响应（ID={dev_id}），请检查 DFU 连接后重试",
    "Post-erase verify failed: region is not all 0xFF": "擦除后校验失败：区域非全 0xFF",
    "Flash verify failed: first difference at 0x{pos:06X}": "烧写校验失败：首个差异在 0x{pos:06X}",
    "No libusb backend found (libusb-1.0.dll): put it in resources/bin/ or "
    "install system libusb-1.0":
        "未找到 libusb 后端（libusb-1.0.dll）：请放入 resources/bin/ 或安装系统 libusb-1.0",
    "Device not found; check the VID/PID or connection state":
        "设备未找到，请检查VID/PID或连接状态",
    "Device not found after reset; check the connection state":
        "复位后未找到设备，请检查连接状态",
    "Required input/output endpoints not found": "未找到所需的输入/输出端点",
    "USB communication error: {e}": "USB通信错误: {e}",

    # ---- fixture / signal source / plan (SLogic32U3 handover) ------------
    'Cannot find the fixture: no 0403:6010 device reports a BL702 serial':
        '找不到夹具：没有任何 0403:6010 设备的序列号含 BL702',
    'openFPGALoader could not load the fixture; falling back to the Gowin programmer':
        'openFPGALoader 未能烧录夹具，改用 Gowin 编程器',
    'Gowin programmer_cli not found; cannot load the fixture':
        '未找到 Gowin programmer_cli，无法烧录夹具',
    'Loading fixture with openFPGALoader: {name} (FTDI serial {s})':
        '用 openFPGALoader 烧录夹具：{name}（FTDI 序列号 {s}）',
    'openFPGALoader reported no success marker (rc={rc})':
        'openFPGALoader 未报告成功标记（rc={rc}）',
    'Fixture bitstream not found: {p}':
        '找不到夹具位流：{p}',
    'Fixture programming FAILED (exit {rc})':
        '夹具烧录失败（退出码 {rc}）',
    'Fixture programming timed out after {s}s':
        '夹具烧录超时（{s} 秒）',
    'openFPGALoader timed out after {s}s':
        'openFPGALoader 超时（{s} 秒）',
    'Cannot execute {p}':
        '无法执行 {p}',
    'none of them reports a serial containing "{h}"':
        '没有一个的序列号含 "{h}"',
    '{n} of them report a serial containing "{h}"':
        '其中 {n} 个的序列号含 "{h}"',
    "Multiple 0403:6010 ports found ({names}); {hint}. The Gowin USB programmer is an FT2232H with the same IDs and two ports of its own, so unplug it if you do not need it, or set signal_source.serial explicitly to the fixture's port.":
        '发现多个 0403:6010 串口（{names}）；{hint}。Gowin USB 编程器是同 ID 的 FT2232H，自带两个串口——不需要时请拔掉它，或在 signal_source.serial 中显式指定夹具的串口。',
    'No Tang Nano signal generator found (looking for {vid:#06x}:{pid:#06x})':
        '未找到 Tang Nano 信号发生器（查找 {vid:#06x}:{pid:#06x}）',
    'Configured port {p} is not present':
        '配置的串口 {p} 不存在',
    '{p} opened but the FPGA did not answer; is the bitstream loaded?':
        '{p} 已打开但 FPGA 无应答；位流是否已加载？',
    'Cannot open {p}: {e}':
        '无法打开 {p}：{e}',
    'No confirmation for mode {m} on {p}':
        '{p} 未确认切换到模式 {m}',
    'signal_source.firmware is not declared':
        '未声明 signal_source.firmware',
    '== M{mode} {label} ==':
        '== M{mode} {label} ==',
    '-- {label} ({ms} ms window, {n} samples) --':
        '-- {label}（窗口 {ms} ms，{n} 个采样点）--',
    '   {label}: {verdict}  ({p} ok / {f} fail / {s} skip)':
        '   {label}: {verdict}  （{p} 通过 / {f} 失败 / {s} 覆盖）',
    'could not load the signal generator':
        '信号发生器烧录失败',
    'Signal generator not reachable after flashing: {e}':
        '烧录后信号发生器无法连通：{e}',
    'capture failed':
        '采集失败',
    'PASS':
        'PASS',
    'FAIL':
        'FAIL',
    'signal_source.type is fpga but no firmware is declared; the fixture cannot be reflashed by the tool':
        'signal_source.type 为 fpga 但未声明 firmware，工具无法重烧夹具',
    'signal_source.type {k!r} is not one of fpga/external/none':
        'signal_source.type {k!r} 不是 fpga/external/none 之一',
    'signal_source.channel_order must be a permutation of 0..{n}':
        'signal_source.channel_order 必须是 0..{n} 的一个排列',
    'plan[{i}] has no usable configs':
        'plan[{i}] 没有可用的 configs',
    'capture.threshold_groups[{i}] has an out-of-range channel':
        'capture.threshold_groups[{i}] 含越界通道',
    'plan[{i}].configs[{j}].channels={ch} exceeds num_channels':
        'plan[{i}].configs[{j}].channels={ch} 超过 num_channels',
    'plan[{i}].configs[{j}].samplerate={sr} not in the samplerates list':
        'plan[{i}].configs[{j}].samplerate={sr} 不在 samplerates 列表中',
    'Cannot tell the programmer apart from the fixture: every 0403:6010 device reports a BL702 serial':
        '无法区分编程器与夹具：所有 0403:6010 设备的序列号都含 BL702',
    " The signal-generator fixture is plugged in ({ports}); it shares the FTDI ID 0403:6010 with this product's programmer, so unplug the fixture to flash the board under test.":
        ' 信号发生器夹具已插入（{ports}）；它与本产品的编程器同为 FTDI 0403:6010，请拔掉夹具后再烧录被测板。',
    "openFPGALoader will target the FTDI device with serial {s} ({p}); the fixture's BL702 is excluded by its serial":
        'openFPGALoader 将使用序列号为 {s} 的 FTDI 设备（{p}）；夹具的 BL702 已按序列号排除',
    'cable-index {cable} read the right device, reading eFuse lock state...':
        'cable-index {cable} 读到正确的器件，读 eFuse 锁定状态…',
    '{n} candidate programmer devices found; using the first':
        '找到 {n} 个候选编程器，使用第一个',
    'cable-index {cable} answered, but it is a different device: read {got:#010x}, expected {exp:#010x} -- trying the next cable':
        'cable-index {cable} 有应答但器件不符：读到 {got:#010x}，应为 {exp:#010x}——尝试下一根线缆',
    'cable-index {cable} answered but reported no IDCODE to compare; using it':
        'cable-index {cable} 有应答但未报告可比对的 IDCODE；使用它',

    # ---- stations: JTAG flashing / production test (v0.2.0) ----------------
    'Recognised automatically from the board':
        '由被测板自动识别',
    'Arm the station: each newly connected board is recognised and run automatically':
        '布防：每接入一块被测板即自动识别并执行',
    'Station steps (automatic when armed; ▶ runs one step)':
        '工位步骤（布防后自动执行；▶ 单步执行）',
    'Standby — choose the station and click Start':
        '待机 — 选择工位后点"开始"',
    'Waiting for the board under test…':
        '等待被测板接入…',
    'Board removed; waiting for the next board…':
        '被测板已取下，等待下一块…',
    'Waiting for the next board…':
        '等待下一块被测板…',
    'Station:':
        '工位:',
    'Start':
        '开始',
    'No analyzer detected':
        '未检测到分析仪',
    'programmer.toml with flash + idcode':
        '含 flash 与 idcode 的 programmer.toml',
    'Disarmed':
        '已撤防',
    'JTAG: no board detected':
        'JTAG：未检测到被测板',
    'Remove the board under test':
        '请取下被测板',
    'Connect a board first (click Start to recognise it)':
        '请先接入被测板（点"开始"自动识别）',
    'Station: {s}':
        '工位: {s}',
    '{n} analyzers online; keep only the board under test connected':
        '在线 {n} 台分析仪；请只保留被测板一台',
    'Armed: {station}':
        '已布防：{station}',
    '{station}: {name}':
        '{station}：{name}',
    'JTAG: unknown device (IDCODE {code:#010x}); not a configured product':
        'JTAG：未知器件（IDCODE {code:#010x}），不是已配置的产品',
    'JTAG: {name} (IDCODE {code:#010x}, via {via})':
        'JTAG：{name}（IDCODE {code:#010x}，经 {via}）',
    'USB link':
        'USB 链路',
    'Unknown station: {s}':
        '未知工位：{s}',
    'USB link {got} (needs {need}): OK':
        'USB 链路 {got}（要求 {need}）：合格',
    'No {name} running its app firmware was found':
        '未找到运行应用固件的 {name}',
    'link too slow: {got}, needs {need} (check the cable and the host port)':
        '链路不合格：{got}，要求 {need}（检查线缆与主机接口）',
    '{name} is in DFU mode: the app firmware has not been flashed':
        '{name} 处于 DFU 模式：应用固件尚未烧录',
    'unknown link {name!r} (expected one of {opts})':
        '未知链路 {name!r}（应为 {opts} 之一）',
    'JTAG flashing':
        'JTAG 烧录',
    'Production test':
        '产测',
    'Fixture · flash signal generator':
        '夹具 · 烧录信号发生器',
    'Check USB link':
        '检查 USB 链路',

    # ---- test station: 10G then 5G link passes ---------------------------
    'Check USB link ({link})':
        '检查 USB 链路（{link}）',
    'Re-plug into a {link} port':
        '换插到 {link} 接口',
    "Move the board's USB cable to a {link} port; the test continues when it is detected":
        '请把被测板的 USB 线换插到 {link} 接口；检测到后自动继续测试',
    'Waiting for {link}: seen {devs}':
        '等待 {link} 链路：当前 {devs}',
    'no config for the {link} pass':
        '没有 {link} 轮的采集配置',
    'plan M{mode} has no config for the {link} pass':
        'plan M{mode} 没有 {link} 轮的采集配置',
    'Connect the board under test':
        '请连接被测板',
    'Click Start, then connect the board under test; it is recognised automatically':
        '点击「开始」后接入被测板，自动识别产品',
}
