"""One-click platform prerequisites for the SLogic PT tool.

Linux only: a udev rule granting the logged-in operator access to the SLogic
USB device (VID 0x359F).  Without it libusb can *enumerate* but serial reads
and captures fail with LIBUSB_ERROR_ACCESS.  Needs root -> runs via pkexec
(graphical polkit prompt), with a symmetric remove.

Windows needs nothing: the analyzer carries MS OS 2.0 descriptors, so Windows
binds WinUSB by itself, and the fixture is only talked to over its serial port.

deploy()/remove() stream progress through the given log callback and return a
bool; callers run them off the UI thread.  requirement() is "" on platforms
that need nothing, which is the GUI's cue to hide the whole group.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .i18n import t
from .profiles import PLATFORM_KEY

# --- identities -------------------------------------------------------------
SLOGIC_VID = "359f"                 # SLogic DFU + APP

# --- Linux udev -------------------------------------------------------------
UDEV_PATH = Path("/etc/udev/rules.d/60-sipeed-slogic.rules")
UDEV_RULES = f"""\
# SLogic logic analyzer -- operator access (installed by the SLogic PT tool)
# SLogic USB device (DFU + APP), VID 0x359F
SUBSYSTEM=="usb", ATTR{{idVendor}}=="{SLOGIC_VID}", MODE="0660", TAG+="uaccess"
"""


# --------------------------------------------------------------------------- #
#  what does this platform need?                                              #
# --------------------------------------------------------------------------- #
def requirement() -> str:
    """'udev' | '' (nothing needed)."""
    return "udev" if PLATFORM_KEY == "linux" else ""


def title() -> str:
    return t("Linux device permissions (udev)") if requirement() == "udev" else ""


def hint() -> str:
    if requirement() == "udev":
        return t("Install a udev rule for SLogic (359F) for root-free access; "
                 "deploying pops up an authorization dialog, then replug the "
                 "device to take effect.")
    return ""


def status() -> str:
    """Current state: 'deployed' | 'absent' | 'unknown' (nothing to deploy)."""
    if requirement() == "udev":
        return "deployed" if UDEV_PATH.is_file() else "absent"
    return "unknown"


# --------------------------------------------------------------------------- #
#  public entry points                                                        #
# --------------------------------------------------------------------------- #
def deploy(log_cb) -> bool:
    req = requirement()
    if req == "udev":
        return _udev(log_cb, install=True)
    log_cb("No environment setup needed on this platform.")
    return True


def remove(log_cb) -> bool:
    req = requirement()
    if req == "udev":
        return _udev(log_cb, install=False)
    log_cb("No environment setup needed on this platform.")
    return True


# --------------------------------------------------------------------------- #
#  Linux: udev via pkexec                                                      #
# --------------------------------------------------------------------------- #
def _udev(log_cb, *, install: bool) -> bool:
    if install:
        log_cb(f"Deploying udev rule -> {UDEV_PATH}")
        # heredoc so the rule text is immune to shell quoting
        script = (
            f"install -d -m 0755 {UDEV_PATH.parent} && "
            f"cat > {UDEV_PATH} <<'SLOGIC_EOF'\n{UDEV_RULES}SLOGIC_EOF\n"
            f"udevadm control --reload-rules && udevadm trigger")
    else:
        log_cb(f"Removing udev rule <- {UDEV_PATH}")
        script = (f"rm -f {UDEV_PATH} && "
                  f"udevadm control --reload-rules && udevadm trigger")

    if not shutil.which("pkexec"):
        log_cb("pkexec not found. Run manually with sudo:")
        log_cb(f"    sudo sh -c '{script}'")
        return False

    log_cb("$ pkexec sh -c <script>   (an authorization dialog will appear)")
    try:
        proc = subprocess.run(["pkexec", "sh", "-c", script],
                              capture_output=True, text=True)
    except OSError as e:
        log_cb(f"pkexec failed: {e}")
        return False
    for stream in (proc.stdout, proc.stderr):
        if stream and stream.strip():
            log_cb(stream.strip())
    ok = proc.returncode == 0
    if install:
        log_cb("OK: udev rule deployed. Replug the device to take effect." if ok
               else "FAILED: deploy failed (authorization cancelled or error).")
    else:
        log_cb("OK: udev rule removed." if ok
               else "FAILED: remove failed (authorization cancelled or error).")
    return ok
