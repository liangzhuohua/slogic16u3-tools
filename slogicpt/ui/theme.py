"""Material 3 look without extra dependencies: Fusion + a QPalette + one QSS.

Every colour the UI uses is a semantic token from TOKENS; widgets never carry
hex literals of their own.  Buttons pick a variant with a dynamic property
(`variant` = filled / tonal / outlined / text / danger / icon), labels a role
(`role` = title / secondary / caption / mono), chips a tone.  apply() installs
the palette and stylesheet for "light" or "dark"; widgets that paint
themselves read colours through color() / qcolor().

The mode setting is "auto" (follow the OS), "light" or "dark", stored in
QSettings under "theme".  Dark-mode detection per OS:
  Windows  HKCU ...\\Themes\\Personalize\\AppsUseLightTheme == 0
  Linux    gsettings color-scheme 'prefer-dark' (GNOME 42+) or a *-dark GTK
           theme name
and, when none of those answers, the lightness of the platform's own palette
captured before Fusion replaces it (KDE and most Qt platform themes).
"""
from __future__ import annotations

import subprocess

from PyQt5.QtGui import QColor, QPalette
from PyQt5.QtWidgets import QApplication

from .. import procutil

MODES = ("auto", "light", "dark")

TOKENS: dict[str, dict[str, str]] = {
    "light": {
        "primary": "#0b57d0", "on_primary": "#ffffff",
        "primary_container": "#d3e3fd", "on_primary_container": "#041e49",
        "secondary_container": "#dde3ea", "on_secondary_container": "#151c24",
        "surface": "#f8f9fc", "surface_lowest": "#ffffff",
        "surface_low": "#f2f4f8", "surface_container": "#ebeef3",
        "surface_high": "#e3e6ec",
        "on_surface": "#1b1c1f", "on_surface_variant": "#44474f",
        "outline": "#74777f", "outline_variant": "#c4c6d0",
        "success": "#1e7d3a", "on_success": "#ffffff",
        "success_container": "#c6efd1", "on_success_container": "#04210d",
        "warning": "#8a5300", "on_warning": "#ffffff",
        "warning_container": "#ffddb3", "on_warning_container": "#2b1700",
        "error": "#ba1a1a", "on_error": "#ffffff",
        "error_container": "#ffdad6", "on_error_container": "#410002",
        # the verdict card: saturated in both schemes, readable across a bench
        "pass": "#1e7d3a", "on_pass": "#ffffff",
        "fail": "#ba1a1a", "on_fail": "#ffffff",
    },
    "dark": {
        "primary": "#a8c7fa", "on_primary": "#062e6f",
        "primary_container": "#0842a0", "on_primary_container": "#d3e3fd",
        "secondary_container": "#3a4048", "on_secondary_container": "#dde3ea",
        "surface": "#111318", "surface_lowest": "#0c0e13",
        "surface_low": "#191c20", "surface_container": "#1d2024",
        "surface_high": "#282a2f",
        "on_surface": "#e2e2e9", "on_surface_variant": "#c4c6d0",
        "outline": "#8e9099", "outline_variant": "#44474f",
        "success": "#7fd99a", "on_success": "#00391a",
        "success_container": "#0e5228", "on_success_container": "#c6efd1",
        "warning": "#ffb95c", "on_warning": "#462a00",
        "warning_container": "#653e00", "on_warning_container": "#ffddb3",
        "error": "#ffb4ab", "on_error": "#690005",
        "error_container": "#93000a", "on_error_container": "#ffdad6",
        "pass": "#1b6e33", "on_pass": "#ffffff",
        "fail": "#a8201a", "on_fail": "#ffffff",
    },
}

_scheme = "light"
_platform_dark: bool | None = None     # platform palette, captured before Fusion


def scheme() -> str:
    """The scheme in effect: "light" or "dark"."""
    return _scheme


def color(name: str) -> str:
    return TOKENS[_scheme][name]


def qcolor(name: str, alpha: float = 1.0) -> QColor:
    c = QColor(TOKENS[_scheme][name])
    c.setAlphaF(alpha)
    return c


def mix(a: str, b: str, t: float) -> str:
    """Blend token (or hex) a toward b by t -- M3 state layers (hover = 8 %)."""
    ca = QColor(TOKENS[_scheme].get(a, a))
    cb = QColor(TOKENS[_scheme].get(b, b))
    return QColor(round(ca.red() + (cb.red() - ca.red()) * t),
                  round(ca.green() + (cb.green() - ca.green()) * t),
                  round(ca.blue() + (cb.blue() - ca.blue()) * t)).name()


# ---------------------------------------------------------------- detection

def _quiet(argv: list[str]) -> str:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=2,
                              **procutil.hidden_kwargs()).stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""


def system_is_dark() -> bool:
    if procutil.IS_WINDOWS:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion"
                                r"\Themes\Personalize") as k:
                return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
        except OSError:
            pass
    elif procutil.IS_LINUX:
        out = _quiet(["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"])
        if "dark" in out:
            return True
        if "light" in out:
            return False
        if "-dark" in _quiet(["gsettings", "get", "org.gnome.desktop.interface",
                              "gtk-theme"]).lower():
            return True
    return bool(_platform_dark)


def resolve(mode: str) -> str:
    if mode in ("light", "dark"):
        return mode
    return "dark" if system_is_dark() else "light"


# -------------------------------------------------------------------- apply

def apply(app: QApplication, mode: str) -> None:
    """Install Fusion, the palette and the stylesheet for `mode`."""
    global _scheme, _platform_dark
    if _platform_dark is None:
        _platform_dark = app.palette().color(QPalette.Window).lightness() < 128
    _scheme = resolve(mode)
    app.setStyle("Fusion")
    app.setPalette(_palette())
    app.setStyleSheet(_stylesheet())


def _palette() -> QPalette:
    pal = QPalette()
    roles = {
        QPalette.Window: "surface", QPalette.WindowText: "on_surface",
        QPalette.Base: "surface_lowest", QPalette.AlternateBase: "surface_low",
        QPalette.Text: "on_surface", QPalette.Button: "surface_container",
        QPalette.ButtonText: "on_surface", QPalette.BrightText: "error",
        QPalette.Highlight: "primary", QPalette.HighlightedText: "on_primary",
        QPalette.ToolTipBase: "surface_high", QPalette.ToolTipText: "on_surface",
        QPalette.Link: "primary", QPalette.PlaceholderText: "on_surface_variant",
        QPalette.Light: "surface_high", QPalette.Midlight: "outline_variant",
        QPalette.Mid: "outline", QPalette.Dark: "outline", QPalette.Shadow: "outline",
    }
    for role, tok in roles.items():
        pal.setColor(role, QColor(color(tok)))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, qcolor("on_surface", 0.38))
    return pal


def _stylesheet() -> str:
    c = TOKENS[_scheme]
    hover = lambda base, on: mix(base, on, 0.08)          # noqa: E731
    press = lambda base, on: mix(base, on, 0.14)          # noqa: E731
    disabled_bg = mix("surface", "on_surface", 0.12)
    disabled_fg = mix("surface", "on_surface", 0.38)
    return f"""
QWidget {{ color: {c['on_surface']}; }}
QMainWindow, QDialog {{ background: {c['surface']}; }}
QToolTip {{ background: {c['surface_high']}; color: {c['on_surface']};
    border: 1px solid {c['outline_variant']}; border-radius: 6px; padding: 6px 8px; }}

QLabel {{ background: transparent; }}
QLabel[role="title"] {{ font-size: 18px; font-weight: 600; }}
QLabel[role="secondary"] {{ color: {c['on_surface_variant']}; }}
QLabel[role="caption"] {{ color: {c['on_surface_variant']}; font-size: 12px; }}
QLabel[role="warn-caption"] {{ color: {c['warning']}; font-size: 12px; }}

QFrame#card {{ background: {c['surface_low']}; border: 1px solid {c['outline_variant']};
    border-radius: 12px; }}
QFrame#appbar {{ background: {c['surface_container']}; border: none;
    border-bottom: 1px solid {c['outline_variant']}; }}

QPushButton {{ border-radius: 18px; padding: 7px 18px; min-height: 22px;
    font-weight: 600; border: 1px solid {c['outline']};
    background: transparent; color: {c['primary']}; }}
QPushButton:hover {{ background: {mix('surface', 'primary', 0.08)}; }}
QPushButton:pressed {{ background: {mix('surface', 'primary', 0.14)}; }}
QPushButton:disabled {{ color: {disabled_fg}; border-color: {disabled_bg}; }}
QPushButton[variant="filled"] {{ background: {c['primary']}; color: {c['on_primary']};
    border: none; }}
QPushButton[variant="filled"]:hover {{ background: {hover('primary', 'on_primary')}; }}
QPushButton[variant="filled"]:pressed {{ background: {press('primary', 'on_primary')}; }}
QPushButton[variant="danger"] {{ background: {c['error']}; color: {c['on_error']};
    border: none; }}
QPushButton[variant="danger"]:hover {{ background: {hover('error', 'on_error')}; }}
QPushButton[variant="tonal"] {{ background: {c['secondary_container']};
    color: {c['on_secondary_container']}; border: none; }}
QPushButton[variant="tonal"]:hover {{
    background: {hover('secondary_container', 'on_secondary_container')}; }}
QPushButton[variant="filled"]:disabled, QPushButton[variant="danger"]:disabled,
QPushButton[variant="tonal"]:disabled {{ background: {disabled_bg}; color: {disabled_fg}; }}
QPushButton[variant="text"] {{ border: none; padding: 6px 12px; }}
QPushButton[variant="text"]:disabled {{ background: transparent; }}
QPushButton[variant="icon"] {{ border: none; border-radius: 18px; padding: 6px;
    min-width: 24px; min-height: 24px; color: {c['on_surface_variant']}; }}
QPushButton[variant="icon"]:hover {{ background: {mix('surface_container', 'on_surface', 0.08)}; }}
QPushButton[variant="icon"]::menu-indicator {{ image: none; width: 0; }}

QPushButton[variant="segment"] {{ border-radius: 0; border: 1px solid {c['outline']};
    padding: 6px 16px; color: {c['on_surface']}; font-weight: 500; }}
QPushButton[variant="segment"][first="true"] {{ border-top-left-radius: 18px;
    border-bottom-left-radius: 18px; }}
QPushButton[variant="segment"][last="true"] {{ border-top-right-radius: 18px;
    border-bottom-right-radius: 18px; border-left: none; }}
QPushButton[variant="segment"]:checked {{ background: {c['primary_container']};
    color: {c['on_primary_container']}; font-weight: 600; }}
QPushButton[variant="segment"]:disabled {{ color: {disabled_fg}; }}
QPushButton[variant="segment"]:checked:disabled {{ background: {disabled_bg}; }}

QPushButton[variant="tab"] {{ border: none; border-radius: 0; padding: 10px 18px;
    color: {c['on_surface_variant']}; font-weight: 500;
    border-bottom: 3px solid transparent; }}
QPushButton[variant="tab"]:hover {{ background: {mix('surface', 'on_surface', 0.06)}; }}
QPushButton[variant="tab"]:checked {{ color: {c['primary']};
    border-bottom: 3px solid {c['primary']}; }}

QPushButton[chip="true"] {{ border-radius: 8px; padding: 4px 10px; min-height: 18px;
    font-weight: 500; border: 1px solid {c['outline_variant']};
    background: transparent; color: {c['on_surface_variant']}; text-align: left; }}
QPushButton[chip="true"]:hover {{ background: {mix('surface_container', 'on_surface', 0.06)}; }}
QPushButton[chip="true"][tone="success"] {{ background: {c['success_container']};
    color: {c['on_success_container']}; border: none; }}
QPushButton[chip="true"][tone="warning"] {{ background: {c['warning_container']};
    color: {c['on_warning_container']}; border: none; }}
QPushButton[chip="true"][tone="error"] {{ background: {c['error_container']};
    color: {c['on_error_container']}; border: none; }}
QPushButton[chip="true"][tone="info"] {{ background: {c['primary_container']};
    color: {c['on_primary_container']}; border: none; }}

QComboBox, QLineEdit {{ background: {c['surface_lowest']}; border: 1px solid {c['outline']};
    border-radius: 8px; padding: 6px 10px; min-height: 20px;
    selection-background-color: {c['primary']}; selection-color: {c['on_primary']}; }}
QComboBox:focus, QLineEdit:focus {{ border: 2px solid {c['primary']}; padding: 5px 9px; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{ background: {c['surface_container']}; border: none;
    border-radius: 8px; padding: 4px; outline: none; }}

QPlainTextEdit, QTextEdit {{ background: {c['surface_lowest']}; border: 1px solid
    {c['outline_variant']}; border-radius: 8px; padding: 6px;
    selection-background-color: {c['primary']}; selection-color: {c['on_primary']}; }}

QTableWidget {{ background: {c['surface_lowest']}; border: 1px solid {c['outline_variant']};
    border-radius: 8px; gridline-color: {c['outline_variant']}; }}
QHeaderView::section {{ background: {c['surface_container']}; border: none;
    border-bottom: 1px solid {c['outline_variant']}; padding: 6px; font-weight: 600; }}
QTableCornerButton::section {{ background: {c['surface_container']}; border: none; }}

QMenu {{ background: {c['surface_container']}; border: 1px solid {c['outline_variant']};
    border-radius: 8px; padding: 6px 0; }}
QMenu::item {{ padding: 8px 28px 8px 16px; }}
QMenu::item:selected {{ background: {mix('surface_container', 'on_surface', 0.08)}; }}
QMenu::indicator {{ width: 14px; height: 14px; left: 6px; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle {{ background: {c['outline_variant']}; border-radius: 3px;
    min-height: 32px; min-width: 32px; }}
QScrollBar::handle:hover {{ background: {c['outline']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
"""


def set_prop(widget, name: str, value) -> None:
    """Change a dynamic property that a QSS selector reads, and re-polish so
    the new style takes effect immediately."""
    widget.setProperty(name, value)
    st = widget.style()
    st.unpolish(widget)
    st.polish(widget)
    widget.update()
