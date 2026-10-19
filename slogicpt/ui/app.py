"""Start the GUI: Qt attributes, settings keys, language, theme."""
from __future__ import annotations

import sys

from PyQt5.QtCore import QSettings, Qt
from PyQt5.QtWidgets import QApplication

from ..i18n import set_language
from . import theme


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
    win.resize(1280, 800)
    win.show()
    sys.exit(app.exec_())
