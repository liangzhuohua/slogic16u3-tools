# TODO

## 待验证

- [ ] **JTAG 工位上硬件**：Gowin 烧录器 + 空板（16U3、32U3 各一），验证按 IDCODE 自动识别 →
      eFuse 写锁 → 烧 DFU 底包 → 拔板后自动等待下一块。重点看 `openFPGALoader --detect`
      对 GW5AT 的输出能否被解析（`programmer._parse_ofl_idcodes`），以及 Windows 下 WinUSB 驱动时的表现。
- [ ] **16U3 产测工位**：16U3 走 `[[capture.tests]]` + 外置固定信号源，`[usb] links = ["5G"]`，尚未在新工位流程下实测。
- [ ] **Windows 打包版**：`python build.py` 出 exe，在 Windows 工位跑两个工位各一遍；确认内置
      `libusb-1.0.dll` 能报出链路速率（libusb ≥ 1.0.22），否则会退回读 sigrok 扫描行的 `(10G)`。

## 固件 / 驱动侧

- [ ] **链路降速命令**：产测 32U3 要求 10G 测完再降到 5G 重测，目前固件没有降速命令，
      只能由操作员换插 5G 线。固件若在 `0x30` 设备管理扩展里加"以 Gen1 重新协商"，
      把 `pipeline._await_link` 的人工换口改成发命令即可全自动。
- [ ] **sigrok 驱动实现 `conn=` 设备选择**（2.3.0 仍回 "conn= is not supported for this driver"）：
      支持后产测工位才能同机并测多台。

## 台架 / 夹具

- [ ] M5（夹具 27 路 1 kHz 同时翻转）对接地很敏感：经 USB Hub 接 5G 时出现地弹毛刺。
      产线台架固定为 5G 线直插主机 + 夹具与分析仪之间短粗地线（见 README）。
      可选的根治办法：夹具 RTL 把 M5 填充通道的边沿错开几微秒（期望频率/占空比不变）。
- [ ] 16U3 的 eFuse 密钥 `products/slogic16u3/firmware/efuse.ekey` 在 JTAG 工位放置（机密，不入库）。
