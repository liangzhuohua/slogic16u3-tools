"""A handful of Material Symbols (Apache-2.0), inlined as SVG paths and tinted
with a theme token at render time.  Replaces the emoji the old GUI used, which
render as empty boxes on some Windows / minimal Linux font setups.  QtSvg ships
with PyQt5, so this adds no dependency."""
from __future__ import annotations

from PyQt5.QtCore import QByteArray, QRectF, Qt
from PyQt5.QtGui import QIcon, QPainter, QPixmap
from PyQt5.QtSvg import QSvgRenderer
from PyQt5.QtWidgets import QApplication

from . import theme

_PATHS = {
    "check": "M9 16.17 4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z",
    "close": "M19 6.41 17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 "
             "12 13.41 17.59 19 19 17.59 13.41 12z",
    "play": "M8 5v14l11-7z",
    "stop": "M6 6h12v12H6z",
    "check_circle": "M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 "
                    "12 2zm-2 15-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z",
    "cancel": "M12 2C6.47 2 2 6.47 2 12s4.47 10 10 10 10-4.47 10-10S17.53 2 12 "
              "2zm5 13.59L15.59 17 12 13.41 8.41 17 7 15.59 10.59 12 7 8.41 8.41 "
              "7 12 10.59 15.59 7 17 8.41 13.41 12 17 15.59z",
    "hourglass": "M6 2v6h.01L6 8.01 10 12l-4 4 .01.01H6V22h12v-5.99h-.01L18 "
                 "16l-4-4 4-3.99-.01-.01H18V2H6zm10 14.5V20H8v-3.5l4-4 4 4zm-4-5"
                 "-4-4V4h8v3.5l-4 4z",
    "usb": "M15 7v4h1v2h-3V5h2l-3-4-3 4h2v8H8v-2.07c.7-.37 1.2-1.08 1.2-1.93 "
           "0-1.21-.99-2.2-2.2-2.2-1.21 0-2.2.99-2.2 2.2 0 .85.5 1.56 1.2 1.93V13"
           "c0 1.11.89 2 2 2h3v3.05c-.71.37-1.2 1.1-1.2 1.95 0 1.22.99 2.2 2.2 2.2 "
           "1.21 0 2.2-.98 2.2-2.2 0-.85-.49-1.58-1.2-1.95V15h3c1.11 0 2-.89 2-2v-2"
           "h1V7h-4z",
    "board": "M22 9V7h-2V5c0-1.1-.9-2-2-2H4c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14"
             "c1.1 0 2-.9 2-2v-2h2v-2h-2v-2h2v-2h-2V9h2zm-4 10H4V5h14v14zM6 13h5v4"
             "H6zm6-6h4v3h-4zM6 7h5v5H6zm6 4h4v6h-4z",
    "warning": "M1 21h22L12 2 1 21zm12-3h-2v-2h2v2zm0-4h-2v-4h2v4z",
    "error": "M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2z"
             "m1 15h-2v-2h2v2zm0-4h-2V7h2v6z",
    "info": "M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2z"
            "m1 15h-2v-6h2v6zm0-8h-2V7h2v2z",
    "translate": "M12.87 15.07l-2.54-2.51.03-.03c1.74-1.94 2.98-4.17 3.71-6.53H17"
                 "V4h-7V2H8v2H1v1.99h11.17C11.5 7.92 10.44 9.75 9 11.35 8.07 10.32 "
                 "7.3 9.19 6.69 8h-2c.73 1.63 1.73 3.17 2.98 4.56l-5.09 5.02L4 19l5"
                 "-5 3.11 3.11.76-2.04zM18.5 10h-2L12 22h2l1.12-3h4.75L21 22h2l-4.5"
                 "-12zm-2.62 7 1.62-4.33L19.12 17h-3.24z",
    "contrast": "M12 22c5.52 0 10-4.48 10-10S17.52 2 12 2 2 6.48 2 12s4.48 10 10 "
                "10zm1-17.93c3.94.49 7 3.86 7 7.93s-3.06 7.44-7 7.93V4.07z",
    "tune": "M3 17v2h6v-2H3zM3 5v2h10V5H3zm10 16v-2h8v-2h-8v-2h-2v6h2zM7 9v2H3v2h4"
            "v2h2V9H7zm14 4v-2H11v2h10zm-6-4h2V7h4V5h-4V3h-2v6z",
    "copy": "M16 1H4c-1.1 0-2 .9-2 2v14h2V3h12V1zm3 4H8c-1.1 0-2 .9-2 2v14c0 1.1"
            ".9 2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm0 16H8V7h11v14z",
    "expand_more": "M16.59 8.59 12 13.17 7.41 8.59 6 10l6 6 6-6z",
    "expand_less": "M12 8l-6 6 1.41 1.41L12 10.83l4.59 4.58L18 14z",
    "power": "M16.01 7 16 3h-2v4h-4V3H8v4h-.01C7 6.99 6 7.99 6 8.99v5.49L9.5 18v3"
             "h5v-3l3.5-3.51v-5.5c0-1-1-2-1.99-1.99z",
    "link": "M3.9 12c0-1.71 1.39-3.1 3.1-3.1h4V7H7c-2.76 0-5 2.24-5 5s2.24 5 5 5h4"
            "v-1.9H7c-1.71 0-3.1-1.39-3.1-3.1zM8 13h8v-2H8v2zm9-6h-4v1.9h4c1.71 0 "
            "3.1 1.39 3.1 3.1s-1.39 3.1-3.1 3.1h-4V17h4c2.76 0 5-2.24 5-5s-2.24-5-5"
            "-5z",
}


def pixmap(name: str, token_or_hex: str, size: int = 24) -> QPixmap:
    """Render icon `name` tinted with a theme token (or a #rrggbb), crisp on
    high-DPI screens."""
    app = QApplication.instance()
    dpr = app.devicePixelRatio() if app is not None else 1.0
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    paint(p, name, token_or_hex, QRectF(0, 0, pm.width(), pm.height()))
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def icon(name: str, token_or_hex: str = "on_surface_variant", size: int = 24) -> QIcon:
    return QIcon(pixmap(name, token_or_hex, size))


def paint(painter: QPainter, name: str, token_or_hex: str, rect: QRectF) -> None:
    """Draw an icon straight into a custom-painted widget."""
    fill = theme.TOKENS[theme.scheme()].get(token_or_hex, token_or_hex)
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
           f'<path fill="{fill}" d="{_PATHS[name]}"/></svg>')
    QSvgRenderer(QByteArray(svg.encode())).render(painter, rect)
