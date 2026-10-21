"""Start the GUI: Qt attributes, settings keys, language, theme."""
from __future__ import annotations

import sys

from PyQt5.QtCore import QSettings, Qt
from PyQt5.QtWidgets import QApplication

from ..i18n import set_language
from . import theme


def _keep_running_on_errors(win) -> None:
    """PyQt5 aborts the whole program on an exception raised inside a slot.
    On a production bench one stray bug must not take the station down, so
    print it and put it in the run log instead."""
    import traceback

    def hook(etype, value, tb):
        text = "".join(traceback.format_exception(etype, value, tb))
        sys.stderr.write(text)
        try:
            win._append_log("[internal error] " + text.rstrip())
        except Exception:                                   # noqa: BLE001
            pass

    sys.excepthook = hook


def main() -> None:
    # crisp on high-DPI screens; must precede QApplication
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setOrganizationName("Sipeed")         # QSettings keying
    app.setApplicationName("SLogicPT")
    settings = QSettings()
    lang = settings.value("language", "zh")
    set_language(lang if isinstance(lang, str) else "zh")
    mode = settings.value("theme", "auto")
    theme.apply(app, mode if mode in theme.MODES else "auto")
    font = app.font()
    font.setPointSize(font.pointSize() + 1)
    app.setFont(font)

    from .main_window import MainWindow
    win = MainWindow()
    _keep_running_on_errors(win)
    win.resize(1280, 800)
    win.show()
    sys.exit(app.exec_())
