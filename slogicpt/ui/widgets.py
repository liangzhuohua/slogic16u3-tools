"""Reusable Material-style widgets: chips, the horizontal stepper, the big
result card, the bottom drawer and the custom-capture dialog.

Colours come only from theme tokens; nothing here knows about pipelines or
profiles -- the window feeds plain data in."""
from __future__ import annotations

import math

from PyQt5.QtCore import QModelIndex, QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QFont, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import (
    QAbstractItemView, QButtonGroup, QComboBox, QDialog, QFrame, QGridLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
    QSizePolicy, QSplitter, QSplitterHandle, QStackedWidget, QTableWidget,
    QVBoxLayout, QWidget,
)

from ..i18n import t
from ..pipeline import StepStatus
from . import icons, theme


def button(text: str = "", variant: str = "", icon: str | None = None,
           icon_token: str | None = None, tip: str = "") -> QPushButton:
    """A QPushButton with a QSS variant and an optional tinted icon."""
    b = QPushButton(text)
    if variant:
        b.setProperty("variant", variant)
    if icon:
        tok = icon_token or {"filled": "on_primary", "danger": "on_error",
                             "tonal": "on_secondary_container",
                             "icon": "on_surface_variant"}.get(variant, "primary")
        b.setIcon(icons.icon(icon, tok, 20))
        b.setIconSize(QSize(20, 20))
    if tip:
        b.setToolTip(tip)
    b.setCursor(Qt.PointingHandCursor)
    return b


def label(text: str = "", role: str = "") -> QLabel:
    lab = QLabel(text)
    if role:
        lab.setProperty("role", role)
    return lab


class Chip(QPushButton):
    """Compact status chip: icon + text, tone = neutral/info/success/warning/error."""

    _ICON_TOKEN = {"neutral": "on_surface_variant", "info": "on_primary_container",
                   "success": "on_success_container", "warning": "on_warning_container",
                   "error": "on_error_container"}

    def __init__(self, icon: str = "", clickable: bool = False):
        super().__init__()
        self._icon = icon
        self.setProperty("chip", True)
        self.setIconSize(QSize(16, 16))
        self.setCursor(Qt.PointingHandCursor if clickable else Qt.ArrowCursor)
        if not clickable:
            self.setFocusPolicy(Qt.NoFocus)
        self.set_state("", "neutral")

    def set_state(self, text: str, tone: str = "neutral", icon: str | None = None):
        if icon is not None:
            self._icon = icon
        self.setText(text)
        if self._icon:
            self.setIcon(icons.icon(self._icon, self._ICON_TOKEN[tone], 16))
        theme.set_prop(self, "tone", tone)


# ------------------------------------------------------------------ stepper

_DOT = 34          # step indicator diameter
_TOP = 4           # margin above the indicator


class _StepDot(QWidget):
    """The round indicator of one step; paints its own state."""

    def __init__(self, number: int):
        super().__init__()
        self.number = number
        self.status = StepStatus.PENDING
        self.hover_play = False
        self.angle = 0.0
        self.setFixedSize(_DOT + 4, _DOT + 4)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(2, 2, _DOT, _DOT)
        st = self.status
        fill = {StepStatus.PASSED: "success", StepStatus.FAILED: "error",
                StepStatus.WAITING_USER: "warning",
                StepStatus.RUNNING: "primary"}.get(st)
        if self.hover_play:
            p.setPen(Qt.NoPen)
            p.setBrush(theme.qcolor("primary"))
            p.drawEllipse(r)
            icons.paint(p, "play", "on_primary", r.adjusted(8, 8, -8, -8))
            return
        if st == StepStatus.RUNNING:
            p.setPen(Qt.NoPen)
            p.setBrush(theme.qcolor("primary_container"))
            p.drawEllipse(r)
            pen = QPen(theme.qcolor("primary"), 3)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawArc(r.adjusted(3, 3, -3, -3), int(-self.angle * 16), 100 * 16)
            self._number(p, r, "on_primary_container")
        elif fill:
            p.setPen(Qt.NoPen)
            p.setBrush(theme.qcolor(fill))
            p.drawEllipse(r)
            name = {StepStatus.PASSED: "check", StepStatus.FAILED: "close",
                    StepStatus.WAITING_USER: "hourglass"}[st]
            icons.paint(p, name, "on_" + fill, r.adjusted(8, 8, -8, -8))
        else:
            p.setPen(QPen(theme.qcolor("outline"), 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(r.adjusted(1, 1, -1, -1))
            self._number(p, r, "on_surface_variant")

    def _number(self, p: QPainter, r: QRectF, tok: str):
        f = QFont(self.font())
        f.setPixelSize(14)
        f.setBold(True)
        p.setFont(f)
        p.setPen(theme.qcolor(tok))
        p.drawText(r, Qt.AlignCenter, str(self.number))


class StepNode(QWidget):
    """Indicator + name + caption (fixture requirement, duration) of one step.
    Clicking it asks for a single-step run when the stepper is interactive."""

    clicked = pyqtSignal(str)
    settings = pyqtSignal(str)
    retry = pyqtSignal(str)

    def __init__(self, number: int, sid: str, name: str, req: str, has_settings: bool):
        super().__init__()
        self.sid = sid
        self.name_text = name
        self.interactive = False
        self.setMinimumWidth(104)
        self.setMaximumWidth(200)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, _TOP, 4, 4)
        lay.setSpacing(4)
        self.dot = _StepDot(number)
        lay.addWidget(self.dot, 0, Qt.AlignHCenter)
        self.name = label(name)
        self.name.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.name.setWordWrap(True)
        lay.addWidget(self.name)
        self.req = label(req, "warn-caption")
        self.req.setAlignment(Qt.AlignHCenter)
        self.req.setWordWrap(True)
        self.req.setVisible(bool(req))
        lay.addWidget(self.req)
        self.info = label("", "caption")
        self.info.setAlignment(Qt.AlignHCenter)
        lay.addWidget(self.info)
        self.note = label("", "error-caption")     # why the step failed
        self.note.setAlignment(Qt.AlignHCenter)
        self.note.setWordWrap(True)
        self.note.hide()
        lay.addWidget(self.note)
        # shown only on a failed step, and only when nothing is running;
        # pressed by the operator -- the tool never retries a step by itself
        self.retry_btn = button(t("Retry"), "tonal", "refresh",
                                tip=t("Run this step again (and the steps after "
                                      "it that have not run)"))
        self.retry_btn.clicked.connect(lambda: self.retry.emit(self.sid))
        self.retry_btn.hide()
        lay.addWidget(self.retry_btn, 0, Qt.AlignHCenter)
        if has_settings:
            self.gear = button(t("Test Params"), "text", "tune", "primary")
            self.gear.clicked.connect(lambda: self.settings.emit(self.sid))
            lay.addWidget(self.gear, 0, Qt.AlignHCenter)
        lay.addStretch(1)

    def set_status(self, status: StepStatus, duration: float | None):
        self.dot.status = status
        self.dot.update()
        self.info.setText(f"{duration:.1f}s" if duration else "")
        bold = status in (StepStatus.RUNNING, StepStatus.WAITING_USER)
        f = self.name.font()
        f.setBold(bold)
        self.name.setFont(f)

    def set_interactive(self, on: bool, tip: str):
        self.interactive = on
        self.setCursor(Qt.PointingHandCursor if on else Qt.ArrowCursor)
        self.setToolTip(tip)
        if not on and self.dot.hover_play:
            self.dot.hover_play = False
            self.dot.update()

    def enterEvent(self, e):
        if self.interactive:
            self.dot.hover_play = True
            self.dot.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.dot.hover_play = False
        self.dot.update()
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e):
        if self.interactive and e.button() == Qt.LeftButton and self.rect().contains(e.pos()):
            self.clicked.emit(self.sid)
        super().mouseReleaseEvent(e)


class _Connector(QWidget):
    """Line between two indicators, level with their centres."""

    def __init__(self):
        super().__init__()
        self.done = False
        self.setMinimumWidth(12)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        y = _TOP + 2 + _DOT / 2
        p.setPen(QPen(theme.qcolor("success" if self.done else "outline_variant"), 2))
        p.drawLine(QPointF(0, y), QPointF(self.width(), y))


class Stepper(QWidget):
    """Horizontal Material stepper.  set_steps() lays the steps out,
    set_status() updates one, show_hint() replaces them with a message."""

    run_requested = pyqtSignal(str)
    settings_requested = pyqtSignal(str)
    retry_requested = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.nodes: dict[str, StepNode] = {}
        self._connectors: list[tuple[StepNode, _Connector]] = []
        self._spin = QTimer(self)
        self._spin.setInterval(16)
        self._spin.timeout.connect(self._tick)
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._holder = None
        self.show_hint("")

    def _new_holder(self) -> QHBoxLayout:
        if self._holder is not None:
            self._outer.removeWidget(self._holder)
            self._holder.hide()
            self._holder.deleteLater()
        self._holder = QWidget()
        lay = QHBoxLayout(self._holder)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(0)
        self._outer.addWidget(self._holder)
        self.nodes.clear()
        self._connectors.clear()
        return lay

    def show_hint(self, text: str):
        lay = self._new_holder()
        hint = label(text, "secondary")
        f = hint.font()
        f.setPixelSize(16)
        hint.setFont(f)
        hint.setAlignment(Qt.AlignCenter)
        hint.setWordWrap(True)
        lay.addWidget(hint, 1)
        self._spin.stop()

    def set_steps(self, steps: list[tuple[str, str, str, bool]]):
        """steps: (step_id, name, fixture requirement, has_settings)."""
        lay = self._new_holder()
        if not steps:
            lay.addWidget(label(t("Profile defines no steps"), "secondary"), 1, Qt.AlignCenter)
            return
        prev = None
        for i, (sid, name, req, has_settings) in enumerate(steps, start=1):
            if prev is not None:
                c = _Connector()
                lay.addWidget(c, 1, Qt.AlignTop)
                c.setFixedHeight(_TOP + _DOT + 8)
                self._connectors.append((prev, c))
            node = StepNode(i, sid, name, req, has_settings)
            node.clicked.connect(self.run_requested)
            node.settings.connect(self.settings_requested)
            node.retry.connect(self.retry_requested)
            lay.addWidget(node, 2)
            self.nodes[sid] = node
            prev = node

    def set_status(self, sid: str, status: StepStatus, duration: float | None = None):
        node = self.nodes.get(sid)
        if node is None:
            return
        node.set_status(status, duration)
        for left, c in self._connectors:
            c.done = left.dot.status == StepStatus.PASSED
            c.update()
        running = any(n.dot.status == StepStatus.RUNNING for n in self.nodes.values())
        if running and not self._spin.isActive():
            self._spin.start()
        elif not running:
            self._spin.stop()

    def set_retry(self, sid: str, show: bool):
        node = self.nodes.get(sid)
        if node is not None:
            node.retry_btn.setVisible(show)

    def set_note(self, sid: str, text: str):
        """Short failure reason under a step; the full text is its tooltip."""
        node = self.nodes.get(sid)
        if node is None:
            return
        short = text if len(text) <= 90 else text[:88] + "…"
        node.note.setText(short.replace("; ", "\n"))      # one cause per line
        node.note.setToolTip(text)
        node.note.setVisible(bool(text))

    def set_req(self, sid: str, text: str):
        node = self.nodes.get(sid)
        if node is not None:
            node.req.setText(text)
            node.req.setVisible(bool(text))

    def set_interactive(self, on: bool, tip_for):
        for node in self.nodes.values():
            node.set_interactive(on, tip_for(node))

    def _tick(self):
        for n in self.nodes.values():
            if n.dot.status == StepStatus.RUNNING:
                n.dot.angle = (n.dot.angle + 6) % 360
                n.dot.update()


# --------------------------------------------------------------- result card

class ResultCard(QFrame):
    """The panel in the middle of the window.

    kind: idle / ready / running / waiting / pass / fail.  A header (icon,
    title, subtitle) sits on top of a body the window fills per state --
    readiness checklist, progress, the mode matrix -- then detail and caption.
    PASS / FAIL paint the header as a solid band, so the verdict reads from
    across the bench while the body stays legible; waiting breathes its
    outline (the operator must act); running shows an indeterminate bar."""

    _STYLE = {   # kind -> (background, foreground, icon)
        "idle": ("surface_low", "on_surface_variant", "info"),
        "ready": ("primary_container", "on_primary_container", "usb"),
        "running": ("surface_low", "primary", None),
        "waiting": ("warning_container", "on_warning_container", "hourglass"),
        "pass": ("pass", "on_pass", "check_circle"),
        "fail": ("fail", "on_fail", "cancel"),
    }

    def __init__(self):
        super().__init__()
        self.kind = "idle"
        self._phase = 0.0
        self._anim = QTimer(self)
        self._anim.setInterval(33)
        self._anim.timeout.connect(self._tick)
        self.setMinimumHeight(200)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 18, 28, 20)
        lay.setSpacing(10)
        self.header = QWidget()
        hv = QVBoxLayout(self.header)
        hv.setContentsMargins(0, 0, 0, 0)
        hv.setSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addStretch(1)
        self.icon = QLabel()
        row.addWidget(self.icon)
        self.title = QLabel()
        self.title.setWordWrap(True)
        row.addWidget(self.title)
        row.addStretch(1)
        hv.addLayout(row)
        self.subtitle = QLabel()
        self.subtitle.setAlignment(Qt.AlignCenter)
        self.subtitle.setWordWrap(True)
        hv.addWidget(self.subtitle)
        lay.addWidget(self.header)
        lay.addSpacing(14)                   # clear of the PASS / FAIL band
        self.body = QVBoxLayout()
        self.body.setSpacing(10)
        lay.addLayout(self.body)
        self.detail = QLabel()
        self.detail.setAlignment(Qt.AlignCenter)
        self.detail.setWordWrap(True)
        self.detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.detail)
        lay.addStretch(1)
        self.caption = QLabel()
        self.caption.setAlignment(Qt.AlignCenter)
        self.caption.setWordWrap(True)
        lay.addWidget(self.caption)
        self.set_state("idle", "")

    def add_body(self, w: QWidget) -> None:
        self.body.addWidget(w)

    def set_state(self, kind: str, title: str, subtitle: str = "", detail: str = "",
                  caption: str = "", icon: str | None = None):
        self.kind = kind
        bg, fg, ic = self._STYLE[kind]
        ic = icon or ic
        big = kind in ("pass", "fail")
        self.icon.setVisible(ic is not None)
        if ic:
            self.icon.setPixmap(icons.pixmap(ic, fg, 44 if big else 32))
        tf = QFont(self.font())
        tf.setPixelSize(44 if big else 24)
        tf.setBold(True)
        tf.setLetterSpacing(QFont.PercentageSpacing, 104 if big else 100)
        self.title.setFont(tf)
        sf = QFont(self.font())
        sf.setPixelSize(16)
        self.subtitle.setFont(sf)
        # on PASS / FAIL only the header band is coloured; the body below it
        # uses the normal text colours
        body_fg = "on_surface" if big else fg
        for w, txt, tok in ((self.title, title, fg), (self.subtitle, subtitle, fg),
                            (self.detail, detail, body_fg),
                            (self.caption, caption, body_fg)):
            w.setText(txt)
            w.setVisible(bool(txt))
            w.setStyleSheet(f"color:{theme.color(tok)};")
        cf = QFont(self.font())
        cf.setPixelSize(15)
        cf.setBold(True)
        self.caption.setFont(cf)
        if kind in ("waiting", "running"):
            self._anim.start()
        else:
            self._anim.stop()
        self.update()

    def _tick(self):
        self._phase = (self._phase + 0.033) % 2.0
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        bg = self._STYLE[self.kind][0]
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(r, 16, 16)
        if self.kind in ("pass", "fail"):
            p.fillPath(path, theme.qcolor("surface_low"))
            band = QRectF(r.left(), r.top(), r.width(),
                          self.header.geometry().bottom() + 14 - r.top())
            p.save()
            p.setClipPath(path)
            p.fillRect(band, theme.qcolor(bg))
            p.restore()
            p.setPen(QPen(theme.qcolor(bg), 2))
            p.drawPath(path)
        else:
            p.fillPath(path, theme.qcolor(bg))
        if self.kind == "waiting":
            a = 0.35 + 0.65 * (0.5 - 0.5 * math.cos(self._phase * math.pi))
            p.setPen(QPen(theme.qcolor("warning", a), 3))
            p.drawPath(path)
        elif self.kind in ("idle", "running"):
            p.setPen(QPen(theme.qcolor("outline_variant"), 1))
            p.drawPath(path)
        if self.kind == "running":
            # indeterminate progress along the bottom edge, clipped to the card
            p.setClipPath(path)
            h = 4
            track = QRectF(r.left(), r.bottom() - h, r.width(), h)
            p.fillRect(track, theme.qcolor("primary_container"))
            seg = r.width() * 0.3
            x = r.left() - seg + (r.width() + seg) * (self._phase / 2.0)
            p.fillRect(QRectF(x, track.top(), seg, h), theme.qcolor("primary"))
        p.end()
        super().paintEvent(e)


class _Cell(QWidget):
    """One matrix cell: pending / running / pass / fail."""

    def __init__(self):
        super().__init__()
        self.state = "pending"
        self.angle = 0.0
        self.setFixedSize(30, 30)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(3, 3, 24, 24)
        if self.state in ("pass", "fail"):
            tok = "success" if self.state == "pass" else "error"
            p.setPen(Qt.NoPen)
            p.setBrush(theme.qcolor(tok))
            p.drawEllipse(r)
            icons.paint(p, "check" if self.state == "pass" else "close",
                        "on_" + tok, r.adjusted(5, 5, -5, -5))
        elif self.state == "running":
            p.setPen(Qt.NoPen)
            p.setBrush(theme.qcolor("primary_container"))
            p.drawEllipse(r)
            pen = QPen(theme.qcolor("primary"), 2.5)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawArc(r.adjusted(2, 2, -2, -2), int(-self.angle * 16), 100 * 16)
        else:
            p.setPen(QPen(theme.qcolor("outline_variant"), 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(r.adjusted(4, 4, -4, -4))


class ModeMatrix(QWidget):
    """Link passes x (link check + pattern modes), filled in as they finish.
    Hovering a failed cell shows why it failed."""

    def __init__(self):
        super().__init__()
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(10)
        self._grid.setVerticalSpacing(6)
        self.cells: dict[tuple[str, str], _Cell] = {}
        self._spin = QTimer(self)
        self._spin.setInterval(16)
        self._spin.timeout.connect(self._tick)

    def set_layout(self, rows: list[str], cols: list[tuple[str, str, str]]):
        """rows: link names ("" for a product without link passes);
        cols: (key, header, tooltip)."""
        while self._grid.count():
            w = self._grid.takeAt(0).widget()
            if w is not None:
                w.hide()                 # deleteLater only acts later
                w.deleteLater()
        self.cells.clear()
        for c in range(self._grid.columnCount()):     # forget the old layout
            self._grid.setColumnStretch(c, 0)
        self._grid.setColumnStretch(0, 1)
        for c, (key, head, tip) in enumerate(cols, start=1):
            h = label(head, "caption")
            h.setAlignment(Qt.AlignCenter)
            h.setToolTip(tip)
            self._grid.addWidget(h, 0, c, Qt.AlignCenter)
        self._grid.setColumnStretch(len(cols) + 1, 1)
        for r, row in enumerate(rows, start=1):
            rl = label(row, "secondary")
            rl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._grid.addWidget(rl, r, 0)
            for c, (key, _h, _t) in enumerate(cols, start=1):
                cell = _Cell()
                self._grid.addWidget(cell, r, c, Qt.AlignCenter)
                self.cells[(row, key)] = cell

    def set_cell(self, row: str, key: str, state: str, tip: str = ""):
        cell = self.cells.get((row, key))
        if cell is None:
            return
        cell.state = state
        cell.setToolTip(tip)
        cell.update()
        running = any(x.state == "running" for x in self.cells.values())
        if running and not self._spin.isActive():
            self._spin.start()
        elif not running:
            self._spin.stop()

    def _tick(self):
        for cell in self.cells.values():
            if cell.state == "running":
                cell.angle = (cell.angle + 6) % 360
                cell.update()


class Checklist(QWidget):
    """Readiness rows: an ok / bad / info icon, a name and a detail."""

    _ICON = {"ok": ("check_circle", "success"), "bad": ("error", "error"),
             "info": ("info", "on_surface_variant")}

    def __init__(self):
        super().__init__()
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(10)
        self._grid.setVerticalSpacing(6)
        self._grid.setColumnStretch(0, 1)
        self._grid.setColumnStretch(4, 1)
        self._last = None

    def set_items(self, items: list[tuple[str, str, str]]):
        if items == self._last:
            return
        self._last = list(items)
        while self._grid.count():
            w = self._grid.takeAt(0).widget()
            if w is not None:
                w.hide()                 # deleteLater only acts later
                w.deleteLater()
        for r, (state, name, detail) in enumerate(items):
            ic, tok = self._ICON[state]
            i = QLabel()
            i.setPixmap(icons.pixmap(ic, tok, 20))
            self._grid.addWidget(i, r, 1)
            n = label(name)
            f = n.font()
            f.setBold(True)
            n.setFont(f)
            self._grid.addWidget(n, r, 2)
            d = label(detail, "secondary")
            self._grid.addWidget(d, r, 3)


# -------------------------------------------------------------------- drawer

class _GripHandle(QSplitterHandle):
    """Splitter handle drawn as a short centred pill (M3 drag handle);
    nothing is drawn while disabled."""

    def paintEvent(self, _e):
        if not self.isEnabled():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = 48, 4
        r = QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)
        tok = "outline" if self.underMouse() else "outline_variant"
        p.setPen(Qt.NoPen)
        p.setBrush(theme.qcolor(tok))
        p.drawRoundedRect(r, 2, 2)

    def enterEvent(self, e):
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.update()
        super().leaveEvent(e)


class GripSplitter(QSplitter):
    def createHandle(self):
        return _GripHandle(self.orientation(), self)


class Drawer(QFrame):
    """Bottom panel with tabs; clicking the open tab collapses it."""

    toggled = pyqtSignal(int)          # open page index, -1 = collapsed

    def __init__(self):
        super().__init__()
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(8, 0, 8, 0)
        head.setSpacing(0)
        self._head = head
        self._tabs: list[QPushButton] = []
        self._group = QButtonGroup(self)
        self._group.setExclusive(False)
        head.addStretch(1)
        self._actions = QStackedWidget()      # the open page's action button
        self._actions.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        head.addWidget(self._actions)
        self._chevron = button("", "icon", "expand_less")
        self._chevron.clicked.connect(self._toggle_last)
        head.addWidget(self._chevron)
        lay.addLayout(head)
        self.stack = QStackedWidget()
        self.stack.setMinimumHeight(120)
        lay.addWidget(self.stack, 1)          # extra height goes to the page, not the tab bar
        self._last = 0
        self.stack.hide()

    def add_page(self, title: str, widget: QWidget, action: QWidget | None = None) -> int:
        idx = len(self._tabs)
        b = button(title, "tab")
        b.setCheckable(True)
        b.clicked.connect(lambda _c, i=idx: self._clicked(i))
        self._head.insertWidget(idx, b)
        self._group.addButton(b, idx)
        self._tabs.append(b)
        self.stack.addWidget(widget)
        self._actions.addWidget(action or QWidget())
        return idx

    def current(self) -> int:
        return self.stack.currentIndex() if self.stack.isVisible() else -1

    def open(self, idx: int):
        if idx < 0 or idx >= len(self._tabs):
            self._set(-1)
        else:
            self._set(idx)

    def _clicked(self, idx: int):
        self._set(-1 if self.current() == idx else idx)

    def _toggle_last(self):
        self._set(-1 if self.current() >= 0 else self._last)

    def _set(self, idx: int):
        for i, b in enumerate(self._tabs):
            b.setChecked(i == idx)
        if idx >= 0:
            self._last = idx
            self.stack.setCurrentIndex(idx)
            self._actions.setCurrentIndex(idx)
            self.stack.show()
        else:
            self.stack.hide()
        self._actions.setVisible(idx >= 0)
        self._chevron.setIcon(icons.icon("expand_more" if idx >= 0 else "expand_less",
                                         "on_surface_variant", 20))
        self.toggled.emit(idx)


# ------------------------------------------------------------ capture params

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


class ParamDialog(QDialog):
    """Custom-capture parameters (engineering), opened from the capture step."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle(t("Test Params"))
        self.setModal(False)
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 20, 20, 20)
        v.setSpacing(12)
        g = QGridLayout()
        g.setHorizontalSpacing(12)
        g.setVerticalSpacing(8)
        self.channel_combo = QComboBox()
        self.rate_combo = QComboBox()
        self.samples_edit = QLineEdit("1M")
        self.volt_edit = QLineEdit("1.6")
        for i, (text, w) in enumerate(((t("Channels:"), self.channel_combo),
                                       (t("Sample Rate:"), self.rate_combo),
                                       (t("Samples:"), self.samples_edit),
                                       (t("Voltage Threshold (V):"), self.volt_edit))):
            g.addWidget(label(text, "secondary"), i // 2, (i % 2) * 2)
            g.addWidget(w, i // 2, (i % 2) * 2 + 1)
        g.setColumnStretch(1, 1)
        g.setColumnStretch(3, 1)
        v.addLayout(g)
        cap = label(t("Per-channel expected values (double-click to edit; "
                      "sequence steps use profile values):"), "caption")
        cap.setWordWrap(True)
        v.addWidget(cap)
        self.expected_table = ExpectedTable()
        self.expected_table.setColumnCount(2)
        self.expected_table.setHorizontalHeaderLabels(["Freq (Hz)", "Duty (%)"])
        self.expected_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.expected_table.verticalHeader().setDefaultSectionSize(26)
        self.expected_table.verticalHeader().setFixedWidth(36)
        v.addWidget(self.expected_table, 1)
        self.run_btn = button(t("Custom Capture Verify (engineering)"), "filled", "play")
        v.addWidget(self.run_btn, 0, Qt.AlignRight)
        self.resize(520, 480)
