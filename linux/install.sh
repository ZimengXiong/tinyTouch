#!/usr/bin/env bash
# Install the checked-out source into per-user storage, including on Bazzite.
set -euo pipefail
source_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
install_root="${XDG_DATA_HOME:-$HOME/.local/share}/tinyTouch"
bin_root="$HOME/.local/bin"
if [[ "${EUID}" -eq 0 ]]; then
    echo 'Run this installer as your desktop user, without sudo.' >&2
    exit 1
fi
python3 -c 'import sys; assert sys.version_info >= (3, 10), "Python 3.10 or newer is required"'
# Do not overwrite the running helper while it owns a USB connection.
was_active=false
if systemctl --user is-active --quiet tinytouch-helper.service; then
    was_active=true
    systemctl --user stop tinytouch-helper.service
fi
restart_helper() {
    if [[ "$was_active" == true ]]; then
        systemctl --user start tinytouch-helper.service || true
    fi
}
trap restart_helper EXIT
mkdir -p -- "$install_root/macos" "$install_root/linux" "$bin_root"
if [[ "$source_root" != "$install_root" ]]; then
    cp -- "$source_root"/macos/*.py "$install_root/macos/"
    cp -- "$source_root/VERSION" "$install_root/"
    cp -- "$source_root/linux/cli.py" "$source_root/linux/requirements.txt" "$install_root/linux/"
    mkdir -p -- "$install_root/linux/tinytouch_linux" "$install_root/linux/kwin"
    cp -- "$source_root/linux/tinytouch_linux/"*.py "$install_root/linux/tinytouch_linux/"
    cp -- "$source_root/linux/kwin/"*.js "$install_root/linux/kwin/"
fi
python3 -m venv "$install_root/.venv"
"$install_root/.venv/bin/python" -m pip install -r "$install_root/linux/requirements.txt"
# A symlink to a Python entry point preserves spaces and shell metacharacters in paths.
"$install_root/.venv/bin/python" - "$install_root" "$bin_root" <<'PY'
from pathlib import Path
import shlex
import sys
root, binary = map(Path, sys.argv[1:])
launcher = root / 'tinytouch'
launcher.write_text('#!/bin/sh\nexec ' + shlex.quote(str(root / '.venv/bin/python')) + ' ' +
                    shlex.quote(str(root / 'linux/cli.py')) + ' "$@"\n')
launcher.chmod(0o755)
link = binary / 'tinytouch'
if link.exists() and not link.is_symlink():
    raise SystemExit(f'Refusing to overwrite {link}; move the existing executable first.')
link.unlink(missing_ok=True)
link.symlink_to(launcher)
PY
printf '\nInstalled. Run: %s/tinytouch setup --us-keyboard\n' "$bin_root"
printf 'Select a US keyboard layout first. Keep your desktop wallet unlocked.\n'
