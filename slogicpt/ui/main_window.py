"""Main window: app bar -> status chips -> stepper -> big result card ->
bottom drawer (report / log / environment setup).

The window only renders; the station state machine is StationController.
Everything the operator can see is kept in plain data (step states, result
state, log/report text), so a language or theme switch rebuilds the widgets
and restores the screen exactly -- including mid-run.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from PyQt5.QtCore import QSettings, QSize, QTimer
from PyQt5.QtGui import QFontDatabase, QTextCursor
from PyQt5.QtWidgets import (
    QAction, QActionGroup, QApplication, QButtonGroup, QFrame, QHBoxLayout,
    QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from .. import env_setup
from ..i18n import LANGS, get_language, set_language, t
from ..pipeline import STATION_LABELS, STATIONS, StepStatus, sequence_plan
from ..profiles import (
    OUTPUT_DIR, ProductProfile, check_resources, format_rate, load_profiles,
    parse_rate,
)
from ..sigrok import SigrokCli, find_sigrok_binary
from . import icons, report, theme
from .station import StationController
from .widgets import Chip, Drawer, ParamDialog, ResultCard, Stepper, button, label


@dataclass
class _Step:
    label: str
    status: StepStatus = StepStatus.PENDING
    t_start: float | None = None
    duration: float | None = None


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        self.profiles, self.problems = load_profiles()
        sigrok_bin = find_sigrok_binary()
        self.problems += check_resources(self.profiles, sigrok_bin)
        self.ctl = StationController(self.profiles,
                                     SigrokCli(sigrok_bin) if sigrok_bin else None, self)

        # operator-visible state, independent of the widgets showing it
        self._steps: dict[str, _Step] = {}
        self._shown_id: str | None = None
        self._result: tuple[str, dict] = ("idle", {})
        self._caption = ""                  # "", "remove" or "next"
        self._prompt = ""
        self._current_sid = ""
        self._switch_popup: QMessageBox | None = None
        self._params: ParamDialog | None = None

        ctl = self.ctl
        ctl.log.connect(self._append_log)
        ctl.step.connect(self._on_step)
        ctl.prompt.connect(self._on_prompt)
        ctl.prompt_clear.connect(self._on_prompt_clear)
        ctl.manual_switch.connect(self._on_manual_switch)
        ctl.run_started.connect(self._on_run_started)
        ctl.run_finished.connect(self._on_finished)
        ctl.devices_changed.connect(self._on_devices)
        ctl.state_changed.connect(self._on_state)
        ctl.notice.connect(lambda text: self._show_result("waiting", text=text))
        ctl.board_removed.connect(lambda: self._set_caption("next"))
        ctl.env_done.connect(self._on_env_done)

        self.setWindowTitle(t("SLogic Production Test"))
        self.setWindowIcon(icons.icon("usb", "primary", 64))
        self.setMinimumSize(960, 640)
        self._build()
        if not self.profiles:
            QMessageBox.critical(
                self, t("No product profiles"),
                t("No usable product profiles under resources/products/; "
                  "see resources/README.md to add them."))
        ctl.refresh_devices()

    # ================================================================ build

    def _build(self):
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_appbar())
        body = QVBoxLayout()
        body.setContentsMargins(24, 16, 24, 16)
        body.setSpacing(16)
        chips = QHBoxLayout()
        chips.setSpacing(8)
        self.product_chip = Chip("board")
        self.product_chip.setToolTip(t("Recognised automatically from the board"))
        self.device_chip = Chip("usb")
        chips.addWidget(self.product_chip)
        chips.addWidget(self.device_chip)
        chips.addStretch(1)
        body.addLayout(chips)

        step_card = QFrame()
        step_card.setObjectName("card")
        sc = QVBoxLayout(step_card)
        sc.setContentsMargins(8, 8, 8, 8)
        self.stepper = Stepper()
        self.stepper.setMinimumHeight(150)
        self.stepper.run_requested.connect(self._run_single_step)
        self.stepper.settings_requested.connect(self._open_params)
        sc.addWidget(self.stepper)
        body.addWidget(step_card)

        self.result_card = ResultCard()
        body.addWidget(self.result_card, 1)
        body.addWidget(self._build_drawer())
        root.addLayout(body, 1)
        self.setCentralWidget(central)

        if self._params is not None:
            self._params.deleteLater()
        self._params = ParamDialog(self)
        self._params.channel_combo.currentTextChanged.connect(self._on_channels_changed)
        self._params.run_btn.clicked.connect(self._run_capture)

        self._render_problem_chip()
        self._layout_steps(force=True, keep_status=True)
        self._fill_params(self.ctl.profile)
        self._render_result()
        self._on_devices()
        self._on_state()
        self._refresh_env_buttons()

    def _build_appbar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("appbar")
        h = QHBoxLayout(bar)
        h.setContentsMargins(20, 10, 16, 10)
        h.setSpacing(12)
        title = label(t("SLogic Production Test"), "title")
        h.addWidget(title)
        h.addSpacing(12)

        seg = QHBoxLayout()
        seg.setSpacing(0)
        self.station_group = QButtonGroup(self)
        self.station_btns = {}
        for i, st in enumerate(STATIONS):
            b = button(t(STATION_LABELS[st]), "segment")
            b.setCheckable(True)
            b.setProperty("first", i == 0)
            b.setProperty("last", i == len(STATIONS) - 1)
            b.setChecked(st == self.ctl.station)
            b.clicked.connect(lambda _c, s=st: self._on_station(s))
            self.station_group.addButton(b)
            self.station_btns[st] = b
            seg.addWidget(b)
        h.addLayout(seg)
        h.addStretch(1)

        self.warn_chip = Chip("warning", clickable=True)
        self.warn_chip.clicked.connect(self._show_problems)
        h.addWidget(self.warn_chip)

        lang = button("", "icon", "translate", tip=t("Language"))
        menu = QMenu(lang)
        group = QActionGroup(menu)
        for code, name in LANGS:
            a = QAction(name, menu, checkable=True)
            a.setChecked(code == get_language())
            a.triggered.connect(lambda _c, c=code: self._on_language(c))
            group.addAction(a)
            menu.addAction(a)
        lang.setMenu(menu)
        h.addWidget(lang)

        th = button("", "icon", "contrast", tip=t("Theme"))
        menu = QMenu(th)
        group = QActionGroup(menu)
        current = QSettings().value("theme", "auto")
        for mode, text in (("auto", t("Follow system")), ("light", t("Light")),
                           ("dark", t("Dark"))):
            a = QAction(text, menu, checkable=True)
            a.setChecked(mode == current)
            a.triggered.connect(lambda _c, m=mode: self._on_theme(m))
            group.addAction(a)
            menu.addAction(a)
        th.setMenu(menu)
        h.addWidget(th)
        h.addSpacing(4)

        self.action_btn = button("", "filled")
        self.action_btn.setMinimumWidth(112)
        self.action_btn.clicked.connect(self._on_action)
        h.addWidget(self.action_btn)
        return bar

    def _build_drawer(self) -> Drawer:
        d = Drawer()
        mono = QFontDatabase.systemFont(QFontDatabase.FixedFont)

        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(12, 4, 12, 12)
        row = QHBoxLayout()
        row.addWidget(label(t("Session report (paste directly into feedback):"), "caption"))
        row.addStretch(1)
        self.copy_btn = button(t("Copy Report"), "text", "copy", "primary")
        self.copy_btn.clicked.connect(self._copy_report)
        row.addWidget(self.copy_btn)
        v.addLayout(row)
        self.report_box = QPlainTextEdit()
        self.report_box.setReadOnly(True)
        self.report_box.setFont(mono)
        v.addWidget(self.report_box, 1)
        d.add_page(t("Report"), page)

        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(12, 4, 12, 12)
        row = QHBoxLayout()
        self.step_caption = label("", "caption")
        row.addWidget(self.step_caption)
        row.addStretch(1)
        clear = button(t("Clear"), "text")
        clear.clicked.connect(lambda: self.log_box.clear())
        row.addWidget(clear)
        v.addLayout(row)
        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setFont(mono)
        self.log_box.setMaximumBlockCount(20000)
        v.addWidget(self.log_box, 1)
        d.add_page(t("Run Log"), page)

        self.env_deploy_btn = self.env_remove_btn = None
        if env_setup.requirement():
            page = QWidget()
            v = QVBoxLayout(page)
            v.setContentsMargins(16, 12, 16, 16)
            v.setSpacing(12)
            v.addWidget(label(t("Environment Setup (one-time) · {name}").format(
                name=env_setup.title()), "title"))
            hint = label(env_setup.hint(), "secondary")
            hint.setWordWrap(True)
            v.addWidget(hint)
            row = QHBoxLayout()
            self.env_deploy_btn = button(t("Deploy"), "filled")
            self.env_deploy_btn.clicked.connect(lambda: self._env("deploy"))
            self.env_remove_btn = button(t("Remove"))
            self.env_remove_btn.clicked.connect(lambda: self._env("remove"))
            row.addWidget(self.env_deploy_btn)
            row.addWidget(self.env_remove_btn)
            row.addStretch(1)
            v.addLayout(row)
            v.addStretch(1)
            d.add_page(t("Environment Setup"), page)

        saved = QSettings().value("drawer", -1)
        try:
            d.open(int(saved))
        except (TypeError, ValueError):
            d.open(-1)
        d.toggled.connect(lambda i: QSettings().setValue("drawer", i))
        self.drawer = d
        return d

    def _rebuild(self):
        """Rebuild every widget (new language / theme) and restore the screen."""
        log_text = self.log_box.toPlainText()
        report_text = self.report_box.toPlainText()
        params_visible = self._params is not None and self._params.isVisible()
        self._build()
        self.log_box.setPlainText(log_text)
        self.log_box.moveCursor(QTextCursor.End)
        self.report_box.setPlainText(report_text)
        self.setWindowTitle(t("SLogic Production Test"))
        if params_visible:
            self._open_params()

    # ============================================================ rendering

    def _render_problem_chip(self):
        errors = sum(p.severity == "error" for p in self.problems)
        warnings = len(self.problems) - errors
        if not self.problems:
            self.warn_chip.hide()
            return
        parts = ([t("{n} errors").format(n=errors)] if errors else []) + \
                ([t("{n} warnings").format(n=warnings)] if warnings else [])
        self.warn_chip.set_state(" / ".join(parts), "error" if errors else "warning",
                                 "error" if errors else "warning")
        self.warn_chip.setToolTip(t("Click for resource self-check details"))
        self.warn_chip.show()

    def _show_problems(self):
        box = QMessageBox(self)
        box.setWindowTitle(t("Resource Self-check"))
        box.setIcon(QMessageBox.Warning if self.problems else QMessageBox.Information)
        box.setText(t("Startup self-check found the following issues (missing items "
                      "disable the related function; add resources and restart to "
                      "take effect):"))
        box.setDetailedText("\n\n".join(str(p) for p in self.problems) or t("No issues"))
        box.exec_()

    def _on_devices(self):
        ctl = self.ctl
        if not ctl.backend_ok:
            # without a libusb backend every usb.core.find fails silently and no
            # device is ever seen; say so (typical: libusb-1.0.dll missing)
            self.device_chip.set_state(
                t("libusb backend not found (libusb-1.0.dll): cannot enumerate "
                  "USB devices, DFU/APP detection disabled — see resources/README.md"),
                "error", "warning")
        elif ctl.station == "test":
            if not ctl.detected:
                self.device_chip.set_state(t("No analyzer detected"), "error", "usb")
            else:
                self.device_chip.set_state(
                    t("Device: {dev}").format(dev=", ".join(str(d) for d in ctl.detected)),
                    "success" if ctl.test_link_ok() else "warning", "usb")
        elif ctl.last_detect is None and not ctl.armed:
            self.device_chip.set_state(t("JTAG: detected after Start"), "neutral", "link")
        else:
            res = ctl.last_detect
            tone = "success" if res is not None and res.profile is not None else "error"
            self.device_chip.set_state(ctl.jtag_text(), tone, "link")
        p = ctl.profile
        self.product_chip.set_state(
            t("Product: {name}").format(name=p.display_name if p else "—"),
            "info" if p else "neutral")
        # keep the step preview in step with what is plugged in (idle only)
        if ctl.profile is None and not ctl.busy:
            self._layout_steps()
        self._update_enablement()

    def _on_state(self):
        ctl = self.ctl
        for st, b in self.station_btns.items():
            b.setChecked(st == ctl.station)
            b.setEnabled(ctl.idle)
        if ctl.busy or ctl.armed:
            self.action_btn.setText(t("Stop"))
            self.action_btn.setIcon(icons.icon("stop", "on_error", 20))
            theme.set_prop(self.action_btn, "variant", "danger")
            self.action_btn.setToolTip("")
        else:
            self.action_btn.setText(t("Start"))
            self.action_btn.setIcon(icons.icon("play", "on_primary", 20))
            theme.set_prop(self.action_btn, "variant", "filled")
        self.action_btn.setIconSize(QSize(20, 20))
        if ctl.armed and ctl.arm_state == "wait_insert" and self._result[0] in ("idle",):
            self._show_result("ready")
        elif not ctl.armed and not ctl.busy and self._result[0] in ("ready", "waiting"):
            self._caption = ""
            self._show_result("idle")
        elif not ctl.armed and self._caption:
            self._set_caption("")
        self._update_enablement()

    def _update_enablement(self):
        ctl = self.ctl
        missing = ctl.missing()
        if not (ctl.busy or ctl.armed):
            self.action_btn.setEnabled(not missing)
            self.action_btn.setToolTip(
                t("Missing: {items}").format(items=", ".join(missing)) if missing else
                t("Arm the station: each newly connected board is recognised and run "
                  "automatically"))
        else:
            self.action_btn.setEnabled(True)
        single = ctl.idle and ctl.profile is not None and not missing
        self.stepper.set_interactive(
            single, lambda n: t("Single step: {label}").format(label=n.name_text)
            if single else ("" if ctl.profile is not None else
                            t("Connect a board first (click Start to recognise it)")))
        if self._params is not None:
            self._params.run_btn.setEnabled(ctl.sampling_possible())

    # ----------------------------------------------------------- the steps

    def _layout_steps(self, force: bool = False, keep_status: bool = False):
        """Lay the stepper out for the recognised board, or preview the steps
        of what is plugged in; with nothing to show, say what to do."""
        ctl = self.ctl
        shown = ctl.profile or ctl.preview_profile()
        sid = shown.id if shown else None
        if not force and sid == self._shown_id:
            return
        self._shown_id = sid
        if shown is None:
            self._steps.clear()
            self.stepper.show_hint(
                t("Connect the board under test") if ctl.station == "test" else
                t("Click Start, then connect the board under test; it is "
                  "recognised automatically"))
            return
        old = self._steps if keep_status else {}
        plan = sequence_plan(shown, ctl.station)
        self._steps = {}
        has_fixture = shown.signal_source.is_fpga
        rows = []
        for step_id, name in plan:
            prev = old.get(step_id)
            self._steps[step_id] = _Step(name, *(
                (prev.status, prev.t_start, prev.duration) if prev else ()))
            rows.append((step_id, name, self._fixture_req(step_id, has_fixture),
                         step_id.startswith("capture")))
        self.stepper.set_steps(rows)
        for step_id, s in self._steps.items():
            self.stepper.set_status(step_id, s.status, s.duration)
        self._update_enablement()

    @staticmethod
    def _fixture_req(sid: str, has_fixture: bool) -> str:
        """What the step needs of the fixture (Tang Nano 9K), shown under it."""
        if not has_fixture:
            return ""
        if sid.startswith("signal"):
            return t("Requires Tang Nano 9K plugged in")
        if sid.startswith(("blank", "wait", "flash", "switch")):
            return t("Requires Tang Nano 9K unplugged")
        return ""

    def _on_step(self, sid: str, status: StepStatus):
        s = self._steps.get(sid)
        if s is not None:
            if status == StepStatus.RUNNING and s.t_start is None:
                s.t_start = time.time()
            if status in (StepStatus.PASSED, StepStatus.FAILED) and s.t_start:
                s.duration = time.time() - s.t_start
            s.status = status
            self.stepper.set_status(sid, status, s.duration)
        # the manual-switch popup closes once the following "wait for device"
        # step is over (device ready / failed)
        if sid.startswith("wait_") and status in (StepStatus.PASSED, StepStatus.FAILED):
            self._close_switch_popup()
        if status == StepStatus.RUNNING:
            self._current_sid = sid
            name = s.label if s else sid
            self.step_caption.setText(t("Current step: {label}").format(label=name))
            if not self._prompt:
                self._show_result("running", sid=sid)

    # ---------------------------------------------------------- the result

    def _show_result(self, kind: str, **data):
        self._result = (kind, data)
        self._render_result()

    def _set_caption(self, which: str):
        self._caption = which
        self._render_result()

    def _render_result(self):
        kind, d = self._result
        ctl = self.ctl
        p = ctl.profile
        caption = {"remove": t("Remove the board under test"),
                   "next": t("Waiting for the next board…")}.get(self._caption, "")
        if kind == "idle":
            self.result_card.set_state(
                "idle", t("Standby"), t("Choose the station and click Start"))
        elif kind == "ready":
            self.result_card.set_state(
                "ready", t("Waiting for the board under test…"),
                t(STATION_LABELS[ctl.station]),
                icon="usb" if ctl.station == "test" else "link")
        elif kind == "running":
            s = self._steps.get(d.get("sid", ""))
            self.result_card.set_state(
                "running", t("Running…"), p.display_name if p else "",
                t("Current step: {label}").format(label=s.label) if s else "")
        elif kind == "waiting":
            self.result_card.set_state("waiting", d["text"])
        else:
            sub = " · ".join(x for x in (p.display_name if p else "",
                                         f"{d['elapsed']:.1f}s") if x)
            failed = [s.label for s in self._steps.values()
                      if s.status == StepStatus.FAILED]
            detail = (t("Failed steps: {items}").format(items=", ".join(failed))
                      if kind == "fail" and failed else "")
            self.result_card.set_state(kind, "PASS" if kind == "pass" else "FAIL",
                                       sub, detail, caption)

    # ============================================================== actions

    def _on_action(self):
        if self.ctl.busy or self.ctl.armed:
            self.ctl.disarm()
        else:
            self._set_caption("")
            self.ctl.arm()
            if self.ctl.armed:
                self._show_result("ready")

    def _on_station(self, st: str):
        if not self.ctl.set_station(st):
            self._on_state()          # refused (busy): restore the checked button
            return
        self.report_box.clear()
        self._caption = ""
        self._show_result("idle")
        self._layout_steps(force=True)
        self._on_devices()

    def _on_language(self, code: str):
        if code == get_language():
            return
        if self.ctl.busy:
            QMessageBox.information(self, t("Cannot switch"),
                                    t("Cannot switch language while a test is running."))
            self._rebuild()           # resets the menu's checked item
            return
        set_language(code)
        QSettings().setValue("language", code)
        self._rebuild()

    def _on_theme(self, mode: str):
        QSettings().setValue("theme", mode)
        theme.apply(QApplication.instance(), mode)
        self._rebuild()

    def _run_single_step(self, sid: str):
        s = self._steps.get(sid)
        self.ctl.run_single_step(sid, s.label if s else sid)

    def _on_run_started(self, full: bool):
        if full:
            # a new board: its own steps, all pending; params for its product
            self._layout_steps(force=True)
            self._fill_params(self.ctl.profile)
            self._caption = ""
            self._on_devices()
        else:
            for step in self.ctl.pipeline.steps:
                s = self._steps.get(step.id)
                if s is not None:
                    s.status, s.t_start, s.duration = StepStatus.PENDING, None, None
                    self.stepper.set_status(step.id, s.status)
        self._prompt = ""
        self._show_result("running")
        self._update_enablement()

    def _on_finished(self, ok: bool, detail: str, elapsed: float):
        self._close_switch_popup()
        # a cancelled run leaves its current step without a final status
        for sid, s in self._steps.items():
            if s.status in (StepStatus.RUNNING, StepStatus.WAITING_USER):
                s.status = StepStatus.SKIPPED
                self.stepper.set_status(sid, s.status, s.duration)
        self._prompt = ""
        self.step_caption.setText("")
        if self.ctl.armed:
            self._caption = "remove"
        self._show_result("pass" if ok else "fail", elapsed=elapsed)
        steps = [(s.status, f"{i}. {s.label}", s.duration)
                 for i, s in enumerate(self._steps.values(), start=1)]
        self.report_box.setPlainText(report.render(
            ok, detail, elapsed, self.ctl.station, self.ctl.profile,
            self.ctl.detected, steps))

    def _on_prompt(self, text: str):
        self._prompt = text
        self._show_result("waiting", text=text)

    def _on_prompt_clear(self):
        self._prompt = ""
        if self.ctl.busy:
            self._show_result("running", sid=self._current_sid)

    def _on_manual_switch(self, text: str):
        """Popup when a mode switch needs a hardware action (non-modal: the
        pipeline keeps waiting for the device; _on_step closes it)."""
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

    def _append_log(self, s: str):
        self.log_box.appendPlainText(s)

    def _copy_report(self):
        QApplication.clipboard().setText(self.report_box.toPlainText())
        self.copy_btn.setText(t("Copied"))
        self.copy_btn.setIcon(icons.icon("check", "primary", 20))
        btn = self.copy_btn

        def restore():
            try:
                btn.setText(t("Copy Report"))
                btn.setIcon(icons.icon("copy", "primary", 20))
            except RuntimeError:      # rebuilt in the meantime
                pass
        QTimer.singleShot(1500, restore)

    # ------------------------------------------------------ custom capture

    def _open_params(self, _sid: str = ""):
        self._params.show()
        self._params.raise_()
        self._params.activateWindow()

    def _fill_params(self, p: ProductProfile | None):
        if p is None:
            return
        dlg = self._params
        dlg.channel_combo.blockSignals(True)
        dlg.channel_combo.clear()
        dlg.channel_combo.addItems([str(c) for c in p.channel_options])
        dlg.channel_combo.setCurrentText(str(p.default_channels))
        dlg.channel_combo.blockSignals(False)
        dlg.samples_edit.setText(p.default_samples)
        dlg.volt_edit.setText(f"{p.voltage_threshold_v:g}")
        self._on_channels_changed(dlg.channel_combo.currentText())
        default_rate = format_rate(p.default_samplerate_hz)
        if dlg.rate_combo.findText(default_rate) >= 0:
            dlg.rate_combo.setCurrentText(default_rate)

    def _on_channels_changed(self, text: str):
        p = self.ctl.profile
        if p is None or not text:
            return
        dlg = self._params
        ch = int(text)
        rates = [format_rate(r) for r in p.legal_rates(ch)]
        current = dlg.rate_combo.currentText()
        dlg.rate_combo.clear()
        dlg.rate_combo.addItems(rates)
        if current in rates:
            dlg.rate_combo.setCurrentText(current)
        else:
            dlg.rate_combo.setCurrentIndex(dlg.rate_combo.count() - 1)
        dlg.expected_table.setRowCount(ch)
        for row in range(ch):
            dlg.expected_table.setItem(row, 0, QTableWidgetItem(f"{p.expected.freq_hz:.0f}"))
            dlg.expected_table.setItem(row, 1, QTableWidgetItem(f"{p.expected.duty_pct:g}"))

    def _run_capture(self):
        p = self.ctl.profile
        dlg = self._params
        if p is None:
            return
        try:
            channels = int(dlg.channel_combo.currentText())
            rate = parse_rate(dlg.rate_combo.currentText())
            volt = float(dlg.volt_edit.text())
            samples = dlg.samples_edit.text().strip() or p.default_samples
            expected_rows = [
                (float(dlg.expected_table.item(r, 0).text()),
                 float(dlg.expected_table.item(r, 1).text()))
                for r in range(channels)]
        except (TypeError, ValueError, AttributeError) as e:
            QMessageBox.warning(self, t("Parameter error"), str(e))
            return
        self._append_log("===== " + t("Custom capture: {ch}ch@{rate}").format(
            ch=channels, rate=format_rate(rate)) + " =====")
        dlg.hide()                    # step aside so the operator sees the result
        self.ctl.run_capture(channels=channels, samplerate_hz=rate, samples=samples,
                             voltage_threshold_v=volt, expected_rows=expected_rows)

    # ------------------------------------------------- environment setup

    def _env(self, action: str):
        self.env_deploy_btn.setEnabled(False)
        self.env_remove_btn.setEnabled(False)
        self.ctl.run_env_setup(action)

    def _on_env_done(self, ok: bool, _action: str):
        self._refresh_env_buttons()
        if ok:
            self.ctl.refresh_devices()    # permissions/driver may now differ

    def _refresh_env_buttons(self):
        """Linux: the rule file is directly checkable; Windows: the WinUSB
        binding isn't cheaply queryable, so both buttons stay enabled."""
        if self.env_deploy_btn is None:
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
        else:
            self.env_deploy_btn.setText(t("Deploy"))
            self.env_deploy_btn.setToolTip("")
            self.env_remove_btn.setToolTip("")

    def closeEvent(self, e):
        self.ctl.disarm()
        super().closeEvent(e)
