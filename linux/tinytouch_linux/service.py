"""Install and control a user service without root access."""
from pathlib import Path
import os
import subprocess
import sys

from .paths import STATE_DIR

SERVICE = "tinytouch-helper.service"


def systemctl(*arguments, check=True):
    result = subprocess.run(["systemctl", "--user", *arguments], check=False,
                            capture_output=True, text=True)
    if check and result.returncode:
        raise RuntimeError("The tinyTouch user service could not be updated. Check systemctl --user status " + SERVICE)
    return result


def helper_active():
    result = systemctl("is-active", SERVICE, check=False)
    if result.returncode not in (0, 3, 4):
        raise RuntimeError("Could not query the tinyTouch user service")
    return result.returncode == 0


def quote_exec(value):
    # systemd performs its own percent and dollar expansions after parsing.
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"').replace(
        "%", "%%").replace("$", "$$") + '"'


def install():
    root = Path(__file__).resolve().parents[2]
    config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    unit = config / "systemd/user" / SERVICE
    unit.parent.mkdir(parents=True, exist_ok=True)
    command = " ".join(quote_exec(value) for value in
                       (sys.executable, root / "macos/tinytouch_helper.py"))
    state_environment = '"XDG_STATE_HOME=' + str(STATE_DIR.parent).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'
    unit.write_text(f'''[Unit]
Description=tinyTouch HID helper
PartOf=graphical-session.target
After=graphical-session.target

[Service]
Type=simple
Environment={state_environment}
ExecStart={command}
Restart=on-failure
RestartSec=2
UMask=0077
NoNewPrivileges=yes

[Install]
WantedBy=graphical-session.target
''', encoding="utf-8")
    systemctl("daemon-reload")
    systemctl("enable", SERVICE)
    systemctl("restart", SERVICE)
