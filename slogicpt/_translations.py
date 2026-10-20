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
    # step row / logs
    "Single step: {label}": "单步执行：{label}",
    # startup
    "No product profiles": "无产品档案",
    "No usable product profiles under resources/products/; "
    "see resources/README.md to add them.":
        "resources/products/ 下没有可用的产品档案，请参照 resources/README.md 放置。",
    # header
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
    # switch button
    # actions / logs
    # env buttons
    "Redeploy": "重新部署",
    "Rule exists; rewrite to update": "规则已存在，可重新写入以更新",
    "Remove the deployed rule": "移除已部署的规则",
    "Not deployed; nothing to remove": "尚未部署，无需移除",
    # language switch / sampling
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
    "Capture {ch}ch@{rate}": "采样验证 {ch}ch@{rate}",
    "Custom capture {ch}ch@{rate}": "自定义采样 {ch}ch@{rate}",
    "Unknown step: {step_id}": "未知步骤: {step_id}",
    "Cancelled": "已取消",
    "Step \"{label}\" failed": "步骤「{label}」失败",
    "Flow aborted: {e}": "流程中止: {e}",
    "Flow exception:": "流程异常:",
    "Flow exception, see log for details": "流程异常，详见日志",
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
    "Fixture flash FAILED":
        "夹具烧录失败",
    "no firmware declared": "未声明固件",
    "== signal generator: {p} patterns pass, {f} fail ==":
        "== 信号发生器: {p} 个模式通过，{f} 个失败 ==",
    "Capture attempt {a}/{n} failed ({e}); retrying":
        "采样第 {a}/{n} 次失败（{e}），重试中",
    "Capture failed: {e}": "采样失败: {e}",

    # The end-of-flow summary that names the wires to go and look at.
    "---- failing channels ({n}) ----": "---- 不通过的通道（{n} 处）----",
    "got": "实测",
    "want": "应为",
    "ok in group": "组内正常",
    # ---- 夹具插入提示 ----------------------------------------------------
    "Please PLUG IN the signal-generator fixture (Tang Nano 9K) and wait -- "
    "the flow continues by itself":
        "请【插上】信号发生器夹具（Tang Nano 9K）的 USB，然后等待 —— 流程会自动继续",
    "Fixture detected ({p}), continuing": "已检测到夹具（{p}），继续",
    "still waiting ({s}s): {m}": "仍在等待（{s} 秒）：{m}",

    # ---- fixture.py ------------------------------------------------------
    "Launch failed: {e}": "启动失败: {e}",

    # ---- profiles.py (self-check Problem messages) -----------------------
    "profile parse failed: {e}": "档案解析失败: {e}",
    "schema_version should be {expected}, actual {actual!r}":
        "schema_version 应为 {expected}, 实际 {actual!r}",
    "product.id ({pid}) does not match directory name ({dirname})":
        "product.id ({pid}) 与目录名 ({dirname}) 不一致",
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

    # ---- sigrok.py --------------------------------------------------------
    "Capture cancelled": "采样已取消",
    "Capture timed out ({s:.0f}s)": "采样超时 ({s:.0f}s)",
    "Device does not support samplerate {rate} (driver wrapped to {wrapped}). "
    "Please fix the product profile capture.samplerates":
        "设备不支持采样率 {rate}（驱动回卷到 {wrapped}）。请修正产品档案 capture.samplerates",
    "sigrok-cli exit code {code}": "sigrok-cli 退出码 {code}",
    "No capture data file produced: {path}": "未产生采样数据文件: {path}",
    "Capture data is less than one full sample": "采样数据不足一个样本",

    # ---- env_setup.py (title/hint only; deploy log stays English) --------
    "Linux device permissions (udev)": "Linux 设备权限 (udev)",
    "Windows driver (WinUSB)": "Windows 驱动 (WinUSB)",
    "Install a udev rule for SLogic (359F) and the FTDI cable (0403:6010) for "
    "root-free access; deploying pops up an authorization dialog, then replug "
    "the device to take effect.":
        "为 SLogic(359F) 与 FTDI 线缆(0403:6010) 安装 udev 规则，免 root 访问；"
        "部署会弹出授权框，之后重新插拔设备生效。",
    "Bind the fixture's FTDI A-channel (0403:6010 interface 0) to WinUSB, which "
    "openFPGALoader needs to load the fixture; deploying pops up a UAC "
    "authorization dialog.":
        "将夹具 FTDI A 通道(0403:6010 接口0)绑定到 WinUSB，openFPGALoader 载入夹具所需；"
        "部署会弹出 UAC 授权框。",

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
    '   {label}: {verdict}  ({p} ok / {f} fail)':
        '   {label}: {verdict}  （{p} 通过 / {f} 失败）',
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

    # ---- station arming (v0.2.0) -------------------------------------------
    'Recognised automatically from the board':
        '由被测板自动识别',
    'Arm the station: each newly connected board is recognised and run automatically':
        '布防：每接入一块被测板即自动识别并执行',
    'Waiting for the board under test…':
        '等待被测板接入…',
    'Board removed; waiting for the next board…':
        '被测板已取下，等待下一块…',
    'Waiting for the next board…':
        '等待下一块被测板…',
    'Start':
        '开始',
    'No analyzer detected':
        '未检测到分析仪',
    'Disarmed':
        '已撤防',
    'Remove the board under test':
        '请取下被测板',
    'Connect a board first (click Start to recognise it)':
        '请先接入被测板（点"开始"自动识别）',
    '{n} analyzers online; keep only the board under test connected':
        '在线 {n} 台分析仪；请只保留被测板一台',
    'USB link':
        'USB 链路',
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
    # ---- Material UI (v0.3.0) ---------------------------------------------
    'SLogic Production Test':
        'SLogic 产测',
    'Failed steps: {items}':
        '失败步骤：{items}',
    'Product: {name}':
        '产品：{name}',
    'Report':
        '报告',
    'Environment Setup':
        '环境准备',
    'Language':
        '语言',
    'Theme':
        '主题',
    'Follow system':
        '跟随系统',
    'Light':
        '浅色',
    'Dark':
        '深色',
    'Armed':
        '已布防',
}
