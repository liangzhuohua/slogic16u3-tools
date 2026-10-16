"""SLogic production station GUI (multi-product, profile-driven).

Production is split into stations; this program runs two of them, chosen in
the header (the choice is remembered):

- JTAG flashing: a blank board on the JTAG programmer gets its eFuse AES key
  and the DFU base image.  The product is recognised from the JTAG IDCODE.
- Production test: a board running its app firmware is checked for its USB
  link (32U3 needs 10G) and then pattern/capture tested.  One board at a time:
  sigrok-cli cannot choose between several analyzers.

(App-firmware OTA happens between the two, in a separate batch web tool.)

START arms the station: every newly connected board is recognised and run
automatically; the verdict stays on the banner until the next board arrives.
STOP disarms (and cancels a run).  While disarmed, each step row's ▶ runs that
step alone on the last recognised board.

- The LEFT side is the station's linear step list (live status + duration).
- The RIGHT side stacks the result report (top) and the live run log (bottom).
- The BOTTOM is a full-width state banner.

All product specifics come from resources/products/*.toml.

UI strings are bilingual (see slogicpt.i18n): English literals are the
source keys, Chinese is looked up in slogicpt._translations. Wrap only
constant literals in t(); interpolate outside via .format().
"""
from __future__ import annotations

import sys
import threading
import time

from PyQt5.QtCore import QModelIndex, QSettings, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDialog, QFrame,
    QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPushButton, QScrollArea, QSplitter, QTableWidget,
    QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from . import device_watch
from . import env_setup
from . import pipeline as pipeline_mod
from . import programmer
from .device_watch import Mode
from .i18n import LANGS, get_language, set_language, t
from .pipeline import STATION_LABELS, STATIONS, StepStatus, sequence_plan
from .profiles import (
    OUTPUT_DIR, ProductProfile, check_resources, format_rate,
    load_profiles, parse_rate,
)
from .sigrok import SigrokCli, find_sigrok_binary

ACCENT = "#1565c0"

# STATUS_STYLE stores the English text key; translate at the read site with
# t(...) so a live language switch (UI rebuild) picks up the new language.
STATUS_STYLE = {
    StepStatus.PENDING: ("○", "#9e9e9e", "Pending"),
    StepStatus.RUNNING: ("▶", "#1565c0", "Running…"),
    StepStatus.WAITING_USER: ("⏳", "#b26a00", "Waiting"),
    StepStatus.PASSED: ("✔", "#2e7d32", "Pass"),
    StepStatus.FAILED: ("✘", "#c62828", "Fail"),
    StepStatus.SKIPPED: ("−", "#9e9e9e", "Skip"),
}


class ExpectedTable(QTableWidget):
    """Edit only on explicit intent: double-click / click-on-selected / F2.
    A click on the blank area drops the current cell so stray keystrokes
    can't silently overwrite the last-clicked value."""

    def __init__(self):
        super().__init__()
        self.setEditTriggers(QAbstractItemView.DoubleClicked
                             | QAbstractItemView.SelectedClicked
                             | QAbstractItemView.EditKeyPressed)

    def mousePressEvent(self, event):
        if not self.indexAt(event.pos()).isValid():
            self.clearSelection()
            self.setCurrentIndex(QModelIndex())
        super().mousePressEvent(event)


class StepRow(QFrame):
    """One row of the test sequence: status glyph, number+name, duration,
    per-step run button (manual mode)."""

    def __init__(self, index: int, step_id: str, label: str, on_run,
                 on_settings=None, req: str = ""):
        super().__init__()
        self.step_id = step_id
        self.label_text = label
        self._t_start: float | None = None
        self.duration_s: float | None = None
        self.status = StepStatus.PENDING

        self.setFrameShape(QFrame.StyledPanel)
        lay = QHBoxLayout()
        lay.setContentsMargins(8, 3, 8, 3)
        lay.setSpacing(6)
        self.icon = QLabel()
        self.icon.setFixedWidth(20)
        self.icon.setAlignment(Qt.AlignCenter)
        self.name = QLabel(f"{index}. {label}")
        self.req = QLabel(req)
        self.req.setStyleSheet("color:#8a6d3b; font-size:11px;")
        self.info = QLabel("")
        self.info.setStyleSheet("color:#777;")
        self.run_btn = QPushButton("▶")
        self.run_btn.setFixedSize(30, 26)
        self.run_btn.setToolTip(t("Single step: {label}").format(label=label))
        self.run_btn.clicked.connect(lambda: on_run(step_id))
        lay.addWidget(self.icon)
        lay.addWidget(self.name, 1)
        if req:
            lay.addWidget(self.req)
        lay.addWidget(self.info)
        # optional ⚙ gear (e.g. custom capture params on the sampling step)
        # opens a popup; only shown when the row supplies a handler.
        if on_settings is not None:
            self.settings_btn = QPushButton("⚙")
            self.settings_btn.setFixedSize(30, 26)
            self.settings_btn.setToolTip(t("Test Params"))
            self.settings_btn.clicked.connect(lambda: on_settings(step_id))
            lay.addWidget(self.settings_btn)
        lay.addWidget(self.run_btn)
        self.setLayout(lay)
        self.set_status(StepStatus.PENDING)

    def reset(self):
        self._t_start = None
        self.duration_s = None
        self.set_status(StepStatus.PENDING)
        self.info.setText("")

    def set_status(self, status: StepStatus):
        if status == StepStatus.RUNNING and self._t_start is None:
            self._t_start = time.time()
        if status in (StepStatus.PASSED, StepStatus.FAILED) and self._t_start:
            self.duration_s = time.time() - self._t_start
            self.info.setText(f"{self.duration_s:.1f}s")
        self.status = status
        glyph, color, text = STATUS_STYLE[status]
        self.icon.setText(glyph)
        self.icon.setStyleSheet(f"color:{color}; font-weight:bold;")
        self.name.setStyleSheet(f"color:{color};" if status != StepStatus.PENDING else "")
        bg = {"passed": "#edf7ee", "failed": "#fdecea",
              "running": "#e8f0fb", "waiting_user": "#fff8e1"}.get(status.value, "")
        self.setStyleSheet(f"QFrame {{ background:{bg or 'transparent'};"
                           f" border:1px solid #d0d0d0; border-radius:4px; }}")
        self.setToolTip(t(text))


class ProductionTestGUI(QWidget):
    log_signal = pyqtSignal(str)
    step_signal = pyqtSignal(str, object)
    prompt_signal = pyqtSignal(str)
    prompt_clear_signal = pyqtSignal()
    manual_switch_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)
    detect_signal = pyqtSignal(object)   # programmer.Detected | None
    env_signal = pyqtSignal(bool, str)   # (ok, action)  action in {deploy,remove}

    def __init__(self):
        super().__init__()
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        self.profiles, self.problems = load_profiles()
        self.sigrok_bin = find_sigrok_binary()
        self.problems += check_resources(self.profiles, self.sigrok_bin)
        self.sigrok = SigrokCli(self.sigrok_bin) if self.sigrok_bin else None
        self.pipeline: pipeline_mod.Pipeline | None = None
        self.detected: list[device_watch.DeviceStatus] = []
        self.step_rows: dict[str, StepRow] = {}
        self._session_t0: float | None = None
        self._prompt_text = ""
        self._prompt_flash_on = True
        self._prompt_timer = QTimer(self)
        self._prompt_timer.timeout.connect(self._flash_prompt_banner)
        self._switch_popup: QMessageBox | None = None  # manual-switch popup (non-modal)
        saved = QSettings().value("station", "test")
        self.station = saved if saved in STATIONS else "test"
        self._profile: ProductProfile | None = None   # last recognised board's product
        self._board_cable: int | None = None          # JTAG cable index of that board
        # arming: idle -> wait_insert -> running -> wait_remove -> wait_insert ...
        self.armed = False
        self.arm_state = "idle"
        self._detect_busy = False
        self._last_detect = None                      # programmer.Detected | None

        self.init_ui()
        # Signals connect ONCE here to stable forwarding slots; a language
        # switch rebuilds the central widget (new log_box/report_box objects),
        # so the slots must dereference self.<widget> at call time, never bind
        # to a specific widget instance.
        self.log_signal.connect(self._append_log)
        self.step_signal.connect(self._on_step)
        self.prompt_signal.connect(self._on_prompt)
        self.prompt_clear_signal.connect(self._on_prompt_clear)
        self.manual_switch_signal.connect(self._on_manual_switch)
        self.finished_signal.connect(self._on_finished)
        self.detect_signal.connect(self._on_detect)
        self.env_signal.connect(self._on_env_done)
        self._refresh_env_buttons()

        if not self.profiles:
            QMessageBox.critical(
                self, t("No product profiles"),
                t("No usable product profiles under resources/products/; "
                  "see resources/README.md to add them."))
        self.device_timer = QTimer(self)
        self.device_timer.timeout.connect(self.refresh_device_status)
        self.device_timer.start(1500)
        # JTAG station: read-only IDCODE poll while armed (a background thread;
        # programmer_cli takes ~1 s per try).
        self.jtag_timer = QTimer(self)
        self.jtag_timer.timeout.connect(self._jtag_poll)
        self.jtag_timer.start(2500)
        self.refresh_device_status()

    # ------------------------------------------------------------------ UI

    def init_ui(self):
        # A single outer layout holds one swappable central widget so the whole
        # UI can be torn down and rebuilt in the new language (see _rebuild_ui).
        self.setWindowTitle("SLogic Production Test")
        self._outer = QVBoxLayout()
        self._outer.setContentsMargins(0, 0, 0, 0)
        self.setLayout(self._outer)
        self._central = self._build_central()
        self._outer.addWidget(self._central)
        self._set_profile(None)

    def _build_central(self) -> QWidget:
        central = QWidget()
        root = QVBoxLayout()
        root.setSpacing(5)
        root.setContentsMargins(8, 6, 8, 6)
        root.addLayout(self._build_header())
        body = QHBoxLayout()
        body.setSpacing(8)
        body.addWidget(self._build_sequence_panel(), 2)
        body.addWidget(self._build_detail_panel(), 3)
        root.addLayout(body, 1)
        root.addWidget(self._build_banner())
        central.setLayout(root)
        self._build_param_dialog()   # off-screen popup; ⚙ on the sampling step
        return central

    def _rebuild_ui(self):
        """Tear down and rebuild the central widget in the current language,
        preserving operator-visible state (station, board, log, report)."""
        log_text = self.log_box.toPlainText()
        report_text = self.report_box.toPlainText()

        self._outer.removeWidget(self._central)
        self._central.deleteLater()
        if getattr(self, "_param_dialog", None) is not None:
            self._param_dialog.deleteLater()   # top-level popup, not in _central
        self._central = self._build_central()
        self._outer.addWidget(self._central)

        if log_text:
            self.log_box.setPlainText(log_text)
        if report_text:
            self.report_box.setPlainText(report_text)
        self._set_profile(self._profile)
        self.refresh_device_status()
        self._refresh_env_buttons()
        self._render_problem_chip()
        self._update_enablement()

    def _build_header(self) -> QHBoxLayout:
        h = QHBoxLayout()
        h.setSpacing(8)
        h.addWidget(QLabel(t("Station:")))
        self.station_combo = QComboBox()
        for st in STATIONS:
            self.station_combo.addItem(t(STATION_LABELS[st]), st)
        self.station_combo.setCurrentIndex(STATIONS.index(self.station))
        self.station_combo.currentIndexChanged.connect(self._on_station_changed)
        h.addWidget(self.station_combo)
        h.addWidget(QLabel(t("Product:")))
        self.product_label = QLabel("—")
        self.product_label.setStyleSheet(f"color:{ACCENT}; font-weight:bold;")
        self.product_label.setToolTip(t("Recognised automatically from the board"))
        h.addWidget(self.product_label)

        self.device_status_label = QLabel("…")
        f = QFont(); f.setBold(True)
        self.device_status_label.setFont(f)
        h.addWidget(self.device_status_label, 1)

        # language selector (moved here from the old config tab); switching
        # rebuilds the whole UI when idle -- see _on_language_changed.  Set the
        # current index BEFORE connecting so restoring it doesn't fire the slot.
        self.lang_combo = QComboBox()
        for code, name in LANGS:
            self.lang_combo.addItem(name, code)
        cur = self.lang_combo.findData(get_language())
        if cur >= 0:
            self.lang_combo.setCurrentIndex(cur)
        self.lang_combo.currentIndexChanged.connect(self._on_language_changed)
        h.addWidget(QLabel("🌐"))
        h.addWidget(self.lang_combo)

        self.warn_chip = QPushButton()
        self.warn_chip.setFlat(True)
        self.warn_chip.clicked.connect(self._show_problems_dialog)
        h.addWidget(self.warn_chip)
        self._render_problem_chip()

        self.start_btn = QPushButton("▶ " + t("Start"))
        f2 = QFont(); f2.setPointSize(f2.pointSize() + 1); f2.setBold(True)
        self.start_btn.setFont(f2)
        self.start_btn.setStyleSheet(
            f"QPushButton {{ background:{ACCENT}; color:white; padding:5px 18px;"
            f" border-radius:4px; }} QPushButton:disabled {{ background:#b8b8b8; }}")
        self.start_btn.setToolTip(
            t("Arm the station: each newly connected board is recognised and run "
              "automatically"))
        self.start_btn.clicked.connect(self.arm)
        h.addWidget(self.start_btn)

        self.stop_btn = QPushButton("■ " + t("Stop"))
        self.stop_btn.setStyleSheet(
            "QPushButton { background:#c62828; color:white; padding:5px 12px;"
            " border-radius:4px; } QPushButton:disabled { background:#b8b8b8; }")
        self.stop_btn.clicked.connect(self.disarm)
        self.stop_btn.setEnabled(False)
        h.addWidget(self.stop_btn)
        return h

    def _build_sequence_panel(self) -> QWidget:
        panel = QGroupBox(t("Station steps (automatic when armed; ▶ runs one step)"))
        panel.setStyleSheet(
            f"QGroupBox {{ border:1px solid #bbb; border-radius:4px; margin-top:10px;"
            f" font-weight:bold; }} QGroupBox::title {{ subcontrol-origin:margin;"
            f" left:8px; padding:0 3px; color:{ACCENT}; }}")
        outer = QVBoxLayout()
        outer.setContentsMargins(4, 8, 4, 6)
        self.seq_container = QVBoxLayout()
        self.seq_container.setSpacing(3)
        holder = QWidget()
        holder.setLayout(self.seq_container)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(holder)
        outer.addWidget(scroll, 1)

        env_box = self._build_env_group()
        if env_box is not None:
            outer.addWidget(env_box)

        panel.setLayout(outer)
        return panel

    def _build_detail_panel(self) -> QWidget:
        """Right side: two always-visible panes (no tabs) -- result report on
        top, live run log on bottom, like the old two-window layout."""
        split = QSplitter(Qt.Vertical)

        # top: session report
        report_page = QWidget(); rv = QVBoxLayout(); rv.setContentsMargins(4, 4, 4, 4)
        rhead = QHBoxLayout()
        rhead.addWidget(QLabel(t("Session report (paste directly into feedback):")))
        rhead.addStretch(1)
        self.copy_report_btn = QPushButton("📋 " + t("Copy Report"))
        self.copy_report_btn.clicked.connect(self.copy_report)
        rhead.addWidget(self.copy_report_btn)
        rv.addLayout(rhead)
        self.report_box = QTextEdit(); self.report_box.setReadOnly(True)
        rv.addWidget(self.report_box, 1)
        report_page.setLayout(rv)
        split.addWidget(report_page)

        # bottom: live log
        log_page = QWidget(); v = QVBoxLayout(); v.setContentsMargins(4, 4, 4, 4)
        head = QHBoxLayout()
        head.addWidget(QLabel(t("Run Log")))   # top-left title, mirrors the report pane
        self.step_label = QLabel(""); self.step_label.setStyleSheet("color:#555;")
        head.addWidget(self.step_label); head.addStretch(1)
        clear = QPushButton(t("Clear")); clear.clicked.connect(lambda: self.log_box.clear())
        head.addWidget(clear)
        v.addLayout(head)
        self.log_box = QTextEdit(); self.log_box.setReadOnly(True)
        v.addWidget(self.log_box, 1)
        log_page.setLayout(v)
        split.addWidget(log_page)

        split.setStretchFactor(0, 2)   # report
        split.setStretchFactor(1, 3)   # log gets a bit more room
        return split

    def _build_env_group(self) -> QGroupBox | None:
        """环境准备（一次性）：本平台的先决条件一键部署/移除——Linux=udev 设备权限；
        Windows=FTDI A 通道 WinUSB 驱动。其它平台返回 None（不显示该组）。"""
        if not env_setup.requirement():
            return None
        env_box = QGroupBox(
            t("Environment Setup (one-time) · {name}").format(name=env_setup.title()))
        eg = QGridLayout(); eg.setContentsMargins(6, 4, 6, 4)
        self.env_hint = QLabel(env_setup.hint())
        self.env_hint.setWordWrap(True)
        self.env_hint.setStyleSheet("color:#555;")
        eg.addWidget(self.env_hint, 0, 0, 1, 2)
        self.env_deploy_btn = QPushButton(t("Deploy"))
        self.env_deploy_btn.setStyleSheet(
            "QPushButton { background:#2e7d32; color:white; font-weight:bold;"
            " padding:4px 14px; border-radius:4px; }"
            " QPushButton:disabled { background:#b8b8b8; }")
        self.env_deploy_btn.clicked.connect(lambda: self._run_env_setup("deploy"))
        self.env_remove_btn = QPushButton(t("Remove"))
        self.env_remove_btn.setStyleSheet(
            "QPushButton { padding:4px 14px; border-radius:4px; }"
            " QPushButton:disabled { color:#999; }")
        self.env_remove_btn.clicked.connect(lambda: self._run_env_setup("remove"))
        eg.addWidget(self.env_deploy_btn, 1, 0)
        eg.addWidget(self.env_remove_btn, 1, 1)
        eg.setColumnStretch(0, 1)
        eg.setColumnStretch(1, 1)
        env_box.setLayout(eg)
        return env_box

    def _build_param_dialog(self) -> None:
        """Build the (hidden) custom-capture parameters popup opened by the ⚙
        gear on the sampling step.  Built as part of _build_central so its
        widgets exist for _on_product_changed and are rebuilt (retranslated)
        on a language switch."""
        dlg = QDialog(self)
        dlg.setWindowTitle(t("Test Params"))
        dlg.setModal(False)
        pv = QVBoxLayout(); pv.setContentsMargins(8, 8, 8, 8)
        cap = QGridLayout(); cap.setSpacing(4)
        cap.addWidget(QLabel(t("Channels:")), 0, 0)
        self.channel_combo = QComboBox()
        self.channel_combo.currentTextChanged.connect(self._on_channels_changed)
        cap.addWidget(self.channel_combo, 0, 1)
        cap.addWidget(QLabel(t("Sample Rate:")), 0, 2)
        self.rate_combo = QComboBox()
        cap.addWidget(self.rate_combo, 0, 3)
        cap.addWidget(QLabel(t("Samples:")), 1, 0)
        self.samples_edit = QLineEdit("1M")
        cap.addWidget(self.samples_edit, 1, 1)
        cap.addWidget(QLabel(t("Voltage Threshold (V):")), 1, 2)
        self.volt_edit = QLineEdit("1.6")
        cap.addWidget(self.volt_edit, 1, 3)
        pv.addLayout(cap)
        self.sampling_btn = QPushButton("▶ " + t("Custom Capture Verify (engineering)"))
        self.sampling_btn.setStyleSheet(
            "QPushButton { background:#2e7d32; color:white; font-weight:bold;"
            " padding:4px; border-radius:4px; }"
            " QPushButton:disabled { background:#b8b8b8; }")
        self.sampling_btn.clicked.connect(self.run_sampling)
        pv.addWidget(self.sampling_btn)
        pv.addWidget(QLabel(
            t("Per-channel expected values (double-click to edit; "
              "sequence steps use profile values):")))
        self.expected_table = ExpectedTable()
        self.expected_table.setColumnCount(2)
        self.expected_table.setHorizontalHeaderLabels(["Freq (Hz)", "Duty (%)"])
        self.expected_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.expected_table.verticalHeader().setDefaultSectionSize(24)
        self.expected_table.verticalHeader().setFixedWidth(34)
        pv.addWidget(self.expected_table, 1)
        dlg.setLayout(pv)
        dlg.resize(460, 420)
        self._param_dialog = dlg

    def _open_params(self, _sid: str | None = None) -> None:
        """Show the custom-capture params popup (⚙ on the sampling step)."""
        self._param_dialog.show()
        self._param_dialog.raise_()
        self._param_dialog.activateWindow()

    def _build_banner(self) -> QLabel:
        self.banner = QLabel(t("Standby"))
        bf = QFont(); bf.setPointSize(20); bf.setBold(True)
        self.banner.setFont(bf)
        self.banner.setAlignment(Qt.AlignCenter)
        self.banner.setFixedHeight(52)
        self._set_banner("idle", t("Standby — choose the station and click Start"))
        return self.banner

    def _set_banner(self, kind: str, text: str):
        colors = {
            "idle": ("#eceff1", "#555"),
            "running": ("#e8f0fb", ACCENT),
            "waiting": ("#fff3cd", "#7a5c00"),
            "pass": ("#1e9e33", "white"),
            "fail": ("#c62828", "white"),
        }
        bg, fg = colors[kind]
        self.banner.setText(text)
        self.banner.setStyleSheet(
            f"background:{bg}; color:{fg}; border-radius:6px; padding:2px;")

    # ------------------------------------------------- problems chip/dialog

    def _render_problem_chip(self):
        errors = sum(p.severity == "error" for p in self.problems)
        warnings = len(self.problems) - errors
        if not self.problems:
            self.warn_chip.hide()
            return
        parts = (["⛔ " + t("{n} errors").format(n=errors)] if errors else []) + \
                (["⚠ " + t("{n} warnings").format(n=warnings)] if warnings else [])
        self.warn_chip.setText(" / ".join(parts))
        color = "#c62828" if errors else "#b26a00"
        self.warn_chip.setStyleSheet(
            f"QPushButton {{ color: {color}; font-weight: bold;"
            f" border: 1px solid {color}; border-radius: 10px; padding: 2px 10px; }}")
        self.warn_chip.setToolTip(t("Click for resource self-check details"))
        self.warn_chip.show()

    def _show_problems_dialog(self):
        box = QMessageBox(self)
        box.setWindowTitle(t("Resource Self-check"))
        box.setIcon(QMessageBox.Warning if self.problems else QMessageBox.Information)
        box.setText(t("Startup self-check found the following issues (missing items "
                      "disable the related function; add resources and restart to "
                      "take effect):"))
        box.setDetailedText("\n\n".join(str(p) for p in self.problems) or t("No issues"))
        box.exec_()

    # ------------------------------------------------------- profile plumbing

    @property
    def profile(self) -> ProductProfile | None:
        """Product of the board last recognised at this station (None before
        the first board).  Chosen by the hardware, never by the operator."""
        return self._profile

    def _set_profile(self, p: ProductProfile | None):
        self._profile = p
        self.product_label.setText(p.display_name if p else "—")
        # before any board is recognised, show the station's steps for the
        # first product (step ids are the same per station, labels close)
        shown = p or (self.profiles[0] if self.profiles else None)
        if shown is not None:
            self._rebuild_sequence(shown)
        if p is None:
            return
        self.channel_combo.blockSignals(True)
        self.channel_combo.clear()
        self.channel_combo.addItems([str(c) for c in p.channel_options])
        self.channel_combo.setCurrentText(str(p.default_channels))
        self.channel_combo.blockSignals(False)
        self.samples_edit.setText(p.default_samples)
        self.volt_edit.setText(f"{p.voltage_threshold_v:g}")
        self._on_channels_changed(self.channel_combo.currentText())
        default_rate = format_rate(p.default_samplerate_hz)
        if self.rate_combo.findText(default_rate) >= 0:
            self.rate_combo.setCurrentText(default_rate)

    def _on_station_changed(self, _idx):
        st = self.station_combo.currentData()
        if st is None or st == self.station:
            return
        if self.armed or (self.pipeline is not None and self.pipeline.running):
            self.station_combo.blockSignals(True)
            self.station_combo.setCurrentIndex(STATIONS.index(self.station))
            self.station_combo.blockSignals(False)
            return
        self.station = st
        QSettings().setValue("station", st)
        self._board_cable = None
        self._last_detect = None
        self._set_profile(None)
        self.report_box.clear()
        self._set_banner("idle", t("Standby — choose the station and click Start"))
        self.refresh_device_status()

    def _rebuild_sequence(self, p: ProductProfile):
        while self.seq_container.count():
            item = self.seq_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.step_rows.clear()
        plan = sequence_plan(p, self.station)
        if not plan:
            self.seq_container.addWidget(QLabel(t("Profile defines no steps")))
        has_fixture = p.signal_source.is_fpga
        for i, (sid, label) in enumerate(plan, start=1):
            # the sampling/capture step carries a ⚙ gear that opens the custom
            # capture-params popup (the old "Test Params" tab).
            on_settings = self._open_params if sid.startswith("capture") else None
            req = self._step_fixture_req(sid, has_fixture)
            row = StepRow(i, sid, label, self.run_single_step, on_settings, req)
            self.seq_container.addWidget(row)
            self.step_rows[sid] = row
        self.seq_container.addStretch(1)

    def _step_fixture_req(self, sid: str, has_fixture: bool) -> str:
        """给步骤行标注它对夹具(Tang Nano 9K)的插拔要求，让操作员一目了然。"""
        if not has_fixture:
            return ""
        if sid.startswith("signal"):
            return "🔌 " + t("Requires Tang Nano 9K plugged in")
        if sid.startswith(("blank", "wait", "flash", "switch")):
            return "🖐 " + t("Requires Tang Nano 9K unplugged")
        return ""

    def _on_channels_changed(self, text: str):
        p = self.profile
        if p is None or not text:
            return
        ch = int(text)
        rates = p.legal_rates(ch)
        current = self.rate_combo.currentText()
        self.rate_combo.clear()
        self.rate_combo.addItems([format_rate(r) for r in rates])
        if current in [format_rate(r) for r in rates]:
            self.rate_combo.setCurrentText(current)
        else:
            self.rate_combo.setCurrentIndex(self.rate_combo.count() - 1)
        self.expected_table.setRowCount(ch)
        for row in range(ch):
            self.expected_table.setItem(row, 0, QTableWidgetItem(f"{p.expected.freq_hz:.0f}"))
            self.expected_table.setItem(row, 1, QTableWidgetItem(f"{p.expected.duty_pct:g}"))

    # --------------------------------------------------------- device status

    def refresh_device_status(self):
        """Poll the USB analyzers.  At the test station this also drives the
        armed state machine (a new board starts a run, its removal re-arms)."""
        if not device_watch.backend_ok():
            # 无 libusb 后端时所有 usb.core.find 静默失败 -> 看不到任何设备；
            # 显式告警而非静默（典型：Windows 未装 libusb-1.0.dll）
            self.device_status_label.setText(
                "⚠ " + t("libusb backend not found (libusb-1.0.dll): cannot enumerate "
                         "USB devices, DFU/APP detection disabled — see "
                         "resources/README.md"))
            self.device_status_label.setStyleSheet("color:#c62828;")
            self.detected = []
            self._update_enablement()
            return
        self.detected = device_watch.scan_devices(self.profiles)
        if self.station == "test":
            self._show_test_status()
            self._test_arm_tick()
        self._update_enablement()

    def _show_test_status(self):
        if not self.detected:
            self._status(t("No analyzer detected"), "#c62828")
            return
        text = ", ".join(str(d) for d in self.detected)
        ok = (len(self.detected) == 1 and self.detected[0].mode == Mode.APP
              and (self.detected[0].profile.min_link or 0) <= (self.detected[0].speed or 0))
        self._status(t("Device: {dev}").format(dev=text), "#2e7d32" if ok else "#b26a00")

    def _status(self, text: str, color: str):
        self.device_status_label.setText(text)
        self.device_status_label.setStyleSheet(f"color:{color};")

    def _station_missing(self) -> list[str]:
        """What this station still lacks before it can be armed."""
        if self.station == "test":
            return [] if self.sigrok is not None else ["sigrok-cli"]
        ok = any(p.programmer is not None and p.programmer.flash is not None
                 and p.programmer.idcode is not None for p in self.profiles)
        return [] if ok else [t("programmer.toml with flash + idcode")]

    def _update_enablement(self):
        busy = self.pipeline is not None and self.pipeline.running
        idle = not busy and not self.armed
        missing = self._station_missing()
        self.station_combo.setEnabled(idle)
        self.start_btn.setEnabled(idle and not missing)
        self.start_btn.setToolTip(
            t("Missing: {items}").format(items=", ".join(missing)) if missing else
            t("Arm the station: each newly connected board is recognised and run "
              "automatically"))
        self.stop_btn.setEnabled(busy or self.armed)
        p = self.profile
        for row in self.step_rows.values():
            row.run_btn.setEnabled(idle and p is not None and not missing)
            row.run_btn.setToolTip("" if p is not None else
                                   t("Connect a board first (click Start to recognise it)"))
        self.sampling_btn.setEnabled(
            idle and self.station == "test" and self.sigrok is not None
            and p is not None
            and any(d.profile.id == p.id and d.mode == Mode.APP for d in self.detected))

    # ------------------------------------------------------------ arming

    def arm(self):
        if self.armed or self._station_missing():
            return
        self.armed = True
        self.arm_state = "wait_insert"
        self.log_signal.emit("===== " + t("Armed: {station}").format(
            station=t(STATION_LABELS[self.station])) + " =====")
        self._set_banner("waiting", t("Waiting for the board under test…"))
        self._update_enablement()
        if self.station == "jtag":
            self._jtag_poll()
        else:
            self.refresh_device_status()

    def disarm(self):
        was_running = self.pipeline is not None and self.pipeline.running
        if was_running:
            self.cancel_pipeline()
        if self.armed:
            self.log_signal.emit(t("Disarmed"))
        self.armed = False
        self.arm_state = "idle"
        if not was_running:
            self._set_banner("idle", t("Standby — choose the station and click Start"))
        self._update_enablement()

    def _run_board(self, p: ProductProfile, cable: int | None):
        """A new board was recognised while armed: run the whole station."""
        self._set_profile(p)
        self._board_cable = cable
        self.arm_state = "running"
        self.log_signal.emit("===== " + t("{station}: {name}").format(
            station=t(STATION_LABELS[self.station]), name=p.display_name) + " =====")
        self._start(pipeline_mod.Pipeline.for_station(
            p, self.station, self.sigrok, self._callbacks(),
            cable_index=cable, all_profiles=self.profiles), reset_rows=True)

    def _board_removed(self):
        self.arm_state = "wait_insert"
        self.log_signal.emit(t("Board removed; waiting for the next board…"))
        self.step_label.setText(t("Waiting for the next board…"))

    def _test_arm_tick(self):
        if not self.armed or (self.pipeline is not None and self.pipeline.running):
            return
        present = self.detected
        if self.arm_state == "wait_remove":
            if not present:
                self._board_removed()
            return
        if self.arm_state != "wait_insert" or not present:
            return
        if len(present) > 1:
            # sigrok-cli would capture from any of them; never guess
            self._set_banner("waiting", t("{n} analyzers online; keep only the board "
                                          "under test connected").format(n=len(present)))
            return
        self._run_board(present[0].profile, None)

    def _jtag_poll(self):
        if (self.station != "jtag" or not self.armed or self._detect_busy
                or (self.pipeline is not None and self.pipeline.running)):
            return
        self._detect_busy = True
        profiles = self.profiles

        def worker():
            try:
                res = programmer.detect_product(profiles)
            except Exception as e:                          # noqa: BLE001
                self.log_signal.emit("[jtag] " + t("exception: {e}").format(e=e))
                res = None
            self.detect_signal.emit(res)

        threading.Thread(target=worker, daemon=True).start()

    def _on_detect(self, res):
        self._detect_busy = False
        if self.station != "jtag":
            return
        prev = self._last_detect
        self._last_detect = res
        if res is None:
            self._status(t("JTAG: no board detected"), "#c62828")
        elif res.profile is None:
            self._status(t("JTAG: unknown device (IDCODE {code:#010x}); not a configured "
                           "product").format(code=res.idcode), "#c62828")
        else:
            self._status(t("JTAG: {name} (IDCODE {code:#010x}, via {via})").format(
                name=res.profile.display_name, code=res.idcode, via=res.via), "#2e7d32")
        changed = (prev is None) != (res is None) or (
            prev is not None and res is not None and prev.idcode != res.idcode)
        if changed and res is not None:
            self.log_signal.emit("[jtag] " + self.device_status_label.text())
        if not self.armed:
            return
        if self.arm_state == "wait_remove":
            if res is None:
                self._board_removed()
            return
        if self.arm_state != "wait_insert" or res is None:
            return
        if res.profile is None:
            self._set_banner("waiting", self.device_status_label.text())
            return
        self._run_board(res.profile, res.cable)

    # -------------------------------------------------------------- actions

    def _callbacks(self) -> pipeline_mod.PipelineCallbacks:
        return pipeline_mod.PipelineCallbacks(
            on_log=self.log_signal.emit,
            on_step=lambda sid, st: self.step_signal.emit(sid, st),
            on_user_prompt=self.prompt_signal.emit,
            on_user_prompt_clear=self.prompt_clear_signal.emit,
            on_manual_switch=self.manual_switch_signal.emit,
            on_finished=lambda ok, rep: self.finished_signal.emit(ok, rep))

    def _start(self, pl: pipeline_mod.Pipeline, *, reset_rows: bool):
        if self.pipeline is not None and self.pipeline.running:
            return
        self.pipeline = pl
        self._session_t0 = time.time()
        if reset_rows:
            for row in self.step_rows.values():
                row.reset()
        else:
            for step in pl.steps:
                if step.id in self.step_rows:
                    self.step_rows[step.id].reset()
        self._set_banner("running", t("Running…"))
        pl.start()
        self._update_enablement()

    def run_single_step(self, step_id: str):
        p = self.profile
        if p is None:
            return
        row = self.step_rows.get(step_id)
        self.log_signal.emit(
            "===== " + t("Single step: {label}").format(
                label=row.label_text if row else step_id) + " =====")
        self._start(pipeline_mod.Pipeline.single_step(
            p, self.sigrok, self._callbacks(), step_id, self.station,
            cable_index=self._board_cable, all_profiles=self.profiles),
            reset_rows=False)

    def _fixture_present(self) -> bool:
        """检测信号发生器夹具(Tang Nano 9K)是否插着。

        夹具的 BL702 和本产品的 Gowin 烧录器共用 FTDI 0403:6010，只有序列号里带
        'BL702' 的才是夹具。
        """
        try:
            import serial.tools.list_ports as lp
        except ImportError:
            return False
        try:
            for p in lp.comports():
                if (p.vid == 0x0403 and p.pid == 0x6010
                        and "BL702" in (p.serial_number or "").upper()):
                    return True
        except Exception:                                    # noqa: BLE001
            return False
        return False

    # ---------------------------------------------------- environment prep
    def _run_env_setup(self, action: str):
        """Deploy/remove the platform prerequisite (udev / WinUSB) off the UI
        thread; both operations pop a system auth dialog (pkexec / UAC)."""
        if not hasattr(self, "env_deploy_btn"):
            return
        self.env_deploy_btn.setEnabled(False)
        self.env_remove_btn.setEnabled(False)
        # env logs stay ASCII/English on purpose: they interleave with output
        # from Windows tools (pnputil/wdi) that we can't force into one locale.
        self.log_signal.emit(
            f"===== env setup: {action} ({env_setup.requirement()}) =====")

        def worker():
            try:
                fn = env_setup.deploy if action == "deploy" else env_setup.remove
                ok = fn(self.log_signal.emit)
            except Exception as e:  # never let the worker die silently
                self.log_signal.emit(f"[env] exception: {e}")
                ok = False
            self.env_signal.emit(ok, action)

        threading.Thread(target=worker, daemon=True).start()

    def _on_env_done(self, ok: bool, action: str):
        self.log_signal.emit(f"env setup {action}: {'OK' if ok else 'FAILED'}")
        self._refresh_env_buttons()
        if ok:
            self.refresh_device_status()      # permissions/driver may now differ

    def _refresh_env_buttons(self):
        """Enable/label deploy & cancel from the current prereq state.  On
        Linux the rule file is directly checkable; on Windows the WinUSB
        binding isn't cheaply queryable, so both stay enabled."""
        if not hasattr(self, "env_deploy_btn"):
            return
        st = env_setup.status()
        self.env_deploy_btn.setEnabled(True)
        self.env_remove_btn.setEnabled(True)
        if st == "deployed":
            self.env_deploy_btn.setText(t("Redeploy"))
            self.env_deploy_btn.setToolTip(t("Rule exists; rewrite to update"))
            self.env_remove_btn.setToolTip(t("Remove the deployed rule"))
        elif st == "absent":
            self.env_deploy_btn.setText(t("Deploy"))
            self.env_deploy_btn.setToolTip("")
            self.env_remove_btn.setEnabled(False)
            self.env_remove_btn.setToolTip(t("Not deployed; nothing to remove"))
        else:  # unknown (Windows)
            self.env_deploy_btn.setText(t("Deploy"))
            self.env_deploy_btn.setToolTip("")
            self.env_remove_btn.setToolTip("")

    def run_sampling(self):
        p = self.profile
        if p is None or self.sigrok is None:
            return
        try:
            channels = int(self.channel_combo.currentText())
            rate = parse_rate(self.rate_combo.currentText())
            volt = float(self.volt_edit.text())
            samples = self.samples_edit.text().strip() or p.default_samples
            expected_rows = [
                (float(self.expected_table.item(r, 0).text()),
                 float(self.expected_table.item(r, 1).text()))
                for r in range(channels)]
        except (TypeError, ValueError, AttributeError) as e:
            QMessageBox.warning(self, t("Parameter error"), str(e))
            return
        self.log_signal.emit(
            "===== " + t("Custom capture: {ch}ch@{rate}").format(
                ch=channels, rate=format_rate(rate)) + " =====")
        self._param_dialog.hide()   # step aside so the operator sees the log
        self._start(pipeline_mod.Pipeline.capture_only(
            p, self.sigrok, self._callbacks(), channels=channels,
            samplerate_hz=rate, samples=samples, voltage_threshold_v=volt,
            expected_rows=expected_rows), reset_rows=False)

    def cancel_pipeline(self):
        if self.pipeline is not None:
            self.pipeline.request_cancel()
            self.log_signal.emit(t("Stop requested…"))

    def copy_report(self):
        QApplication.clipboard().setText(self.report_box.toPlainText())
        self.copy_report_btn.setText(t("Copied") + " ✔")
        QTimer.singleShot(
            1500, lambda: self.copy_report_btn.setText("📋 " + t("Copy Report")))

    # -------------------------------------------------------------- slots

    def _append_log(self, s: str):
        # stable forwarding slot: always targets the CURRENT log_box, which a
        # language switch replaces with a new widget (see _rebuild_ui).
        self.log_box.append(s)

    def _on_language_changed(self, _idx):
        code = self.lang_combo.currentData()
        if code is None or code == get_language():
            return
        if self.pipeline is not None and self.pipeline.running:
            # can't rebuild the UI mid-run; revert the selection and explain
            self.lang_combo.blockSignals(True)
            i = self.lang_combo.findData(get_language())
            if i >= 0:
                self.lang_combo.setCurrentIndex(i)
            self.lang_combo.blockSignals(False)
            QMessageBox.information(
                self, t("Cannot switch"),
                t("Cannot switch language while a test is running."))
            return
        set_language(code)
        QSettings().setValue("language", code)
        self._rebuild_ui()

    def _on_step(self, sid: str, status: StepStatus):
        row = self.step_rows.get(sid)
        if row is not None:
            row.set_status(status)
        # 人工切换弹窗随后续"等待设备"步骤的完成自动关闭（设备已就绪/失败）
        if sid.startswith("wait_") and status in (StepStatus.PASSED,
                                                  StepStatus.FAILED):
            self._close_switch_popup()
        if status == StepStatus.RUNNING:
            label = row.label_text if row else sid
            self.step_label.setText(t("Current step: {label}").format(label=label))
            if not self._prompt_text:
                self._set_banner("running", t("Running — {label}").format(label=label))

    def _on_prompt(self, text: str):
        self._prompt_text = text
        self._set_banner("waiting", f"⏳ {text}")
        self._prompt_flash_on = True
        self._prompt_timer.start(600)

    def _on_prompt_clear(self):
        self._prompt_timer.stop()
        self._prompt_text = ""

    def _flash_prompt_banner(self):
        # 等待/提示状态下让横幅闪烁，确保操作员不会漏看插拔夹具的要求。
        if not self._prompt_text:
            return
        self._prompt_flash_on = not self._prompt_flash_on
        bg = "#f0c36d" if self._prompt_flash_on else "#fff3cd"
        self.banner.setStyleSheet(
            f"background:{bg}; color:#7a5c00; border-radius:6px; padding:2px;")

    def _on_manual_switch(self, text: str):
        """模式切换只剩硬件操作时的弹窗提示（非模态：流水线继续等待目标模式
        设备出现，出现后 _on_step 自动关闭本弹窗）。"""
        self._close_switch_popup()
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle(t("Manual mode switch required"))
        box.setText(text)
        box.setStandardButtons(QMessageBox.Ok)
        box.setModal(False)
        box.show()
        self._switch_popup = box

    def _close_switch_popup(self):
        if self._switch_popup is not None:
            self._switch_popup.close()
            self._switch_popup = None

    def _on_finished(self, ok: bool, report: str):
        self._close_switch_popup()
        elapsed = time.time() - self._session_t0 if self._session_t0 else 0
        fail_steps = [r.label_text for r in self.step_rows.values()
                      if r.status == StepStatus.FAILED]
        if ok:
            self._set_banner("pass", f"PASS  ({elapsed:.1f}s)")
        else:
            summary = (t("; failed steps: {items}").format(items=", ".join(fail_steps))
                       if fail_steps else "")
            self._set_banner("fail", f"FAIL  ({elapsed:.1f}s){summary}")
        self._render_report(ok, report, elapsed)
        self.step_label.setText("")
        self.pipeline = None
        if self.armed:
            # the verdict stays on the banner until the next board arrives
            self.arm_state = "wait_remove"
            self.step_label.setText(t("Remove the board under test"))
        self._update_enablement()

    def _render_report(self, ok: bool, detail: str, elapsed: float):
        p = self.profile
        lines = [
            "==== " + t("SLogic Production Test Report") + " ====",
            t("Time: {ts}").format(ts=time.strftime('%Y-%m-%d %H:%M:%S')),
            t("Station: {s}").format(s=t(STATION_LABELS[self.station])),
            (t("Product: {name} ({id})").format(name=p.display_name, id=p.id)
             if p else t("Product: -")),
            t("Device: {devs}").format(
                devs=', '.join(str(d) for d in self.detected) or t("not detected")),
            t("Overall: {result}  (elapsed {s:.1f}s)").format(
                result='PASS' if ok else 'FAIL', s=elapsed),
            "",
            "-- " + t("Step Status") + " --",
        ]
        for row in self.step_rows.values():
            _, _, text = STATUS_STYLE[row.status]
            dur = f" ({row.duration_s:.1f}s)" if row.duration_s else ""
            lines.append(f"[{t(text)}] {row.name.text()}{dur}")
        if detail:
            lines += ["", "-- " + t("Details") + " --", detail]
        lines += ["", t("To report an issue, copy this report and attach the "
                        "relevant run-log excerpts.")]
        self.report_box.setPlainText("\n".join(lines))


def main() -> None:
    app = QApplication(sys.argv)
    # QSettings keying for the persisted language choice
    app.setOrganizationName("Sipeed")
    app.setApplicationName("SLogicPT")
    saved = QSettings().value("language", "zh")
    set_language(saved if isinstance(saved, str) else "zh")
    font = app.font()
    font.setPointSize(font.pointSize() + 2)
    app.setFont(font)
    gui = ProductionTestGUI()
    gui.resize(1280, 780)
    gui.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
