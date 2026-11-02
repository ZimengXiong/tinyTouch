# tinyTouch HID on Bazzite / KDE Plasma 6 / Wayland

This source port builds on PR #103 (app-specific HID passwords). It reuses the
firmware protocol, authenticated-event handling, encryption, replay protection,
fingerprint enrollment, helper worker manager, and password-selection order.
No firmware change is required for the port itself; the device needs the
protocol-6 firmware supported by the existing CLI.

## Scope

- HID setup, default passwords, per-finger passwords, and app-specific passwords.
- Native Wayland and XWayland windows identified by KWin's `desktopFileName`.
- Existing KDE sessions, including their lock screens after the helper has
  loaded credentials. Initial login after boot is not supported.
- **US keyboard layout only, with printable ASCII passwords up to 160 keys.**
  Select a US layout before setup and keep it selected, including on the lock
  screen. Automatic Linux keyboard-layout translation is not implemented.
- PIV/PAM configuration and GNOME/other compositors are outside this port.

App IDs and focus are a convenience selector, not a security boundary against
other applications running as your user. Passwords still go to whichever field
has focus when the USB keyboard types; focus can change after the lookup.

## Install from this checkout

Run these commands on the Bazzite host in a KDE Wayland terminal, as your normal
user. Use host Python, not a Flatpak terminal sandbox or a distrobox container.
The installer writes to `~/.local/share/tinyTouch` and `~/.local/bin`; it does
not layer packages into Bazzite's system image.

```sh
bash linux/install.sh
~/.local/bin/tinytouch ports
```

Requirements: Python 3.10+, `venv`/pip, a working user systemd session, and an
unlocked Secret Service provider on the session bus. KWallet can provide this
service when its Secret Service support is enabled. There is deliberately no
plaintext fallback and no automatic wallet-unlock prompt from the helper.

If `~/.local/bin` is on your PATH, use `tinytouch` without its full path.

### USB permissions

Install the included rule before setup. Besides granting serial access, it
prevents ModemManager from probing tinyTouch and disables USB autosuspend for
this device. The current firmware reconnects USB on resume, which can otherwise
interrupt authorization or password delivery on Linux:

```sh
sudo install -m 0644 linux/99-tinytouch.rules /etc/udev/rules.d/99-tinytouch.rules
sudo udevadm control --reload-rules
```

Unplug and reconnect tinyTouch. The rule gives the active local user access to
this device's serial interface; it does not grant access to all serial devices
or disable power management for other devices. If you installed an earlier
version of the rule, install this updated version and reconnect again. The
`99-` prefix is required: the distribution's `92_pcscd_ccid.rules` re-enables
autosuspend for smart-card interfaces, including tinyTouch's composite device.
Do not run the CLI or helper as root.

### Setup

Select a US keyboard layout in KDE and unlock your desktop wallet. Then run:

```sh
tinytouch setup --us-keyboard
```

Enter the default password twice. Setup enrolls a first finger only if the
sensor is empty and registers this computer without removing the Mac's host.
Approve configuration with an enrolled finger when prompted. If switching from
PIV to HID, reconnect and run the same setup command again.

Setup installs `tinytouch-helper.service` in your user systemd configuration.
The helper starts with the graphical session and stops with it. No KWin plugin
is permanently installed or enabled: each app lookup briefly loads the included
script and unloads it when finished.

## App passwords

Find the app's exact identifier:

```sh
tinytouch app --delay 3
```

Focus the app during those three seconds. Copy the printed ID rather than
assuming an app name or macOS bundle ID is also its Linux ID. Windows without a
usable desktop file ID cannot receive app overrides in this version.

```sh
tinytouch password --app APP_ID --finger 1
tinytouch password --app APP_ID --finger 2
tinytouch password --list
tinytouch password --app APP_ID --finger 2 --remove
```

Omit `--finger` for an app password shared by all fingers. To change the default
password, run `tinytouch password`; for a finger-only password, use
`tinytouch password --finger 2`.

Selection order is app + finger → app (any finger) → finger → default. App
passwords are disabled while KDE is locked or if any desktop lookup fails.
The finger/default password is still used in those cases. Keep these fallback
passwords appropriate for the lock screen.

The helper checks KDE's lock state before and after a fresh KWin app query.
Only the queried KWin bus owner can report the result, with a per-query token.
The lookup has a 350 ms deadline plus at most 100 ms for script cleanup, within
the firmware's 1.5 s reply window. Forged/replayed events do not trigger lookup.
Neither window titles nor secrets are sent to KWin.

Credentials and pairing keys stay in Secret Service. The index, keyboard setting,
replay state and foreground USB lease live under
`${XDG_STATE_HOME:-~/.local/state}/tinyTouch`. The shared helper loads credentials
when connecting to the device and wipes its mutable buffers when disconnecting.
If the wallet is locked at startup, unlock it and restart the helper. Credentials
already loaded into a running helper remain available when the screen locks.

## Other commands and troubleshooting

```sh
tinytouch status
tinytouch fingers
tinytouch enroll 2
tinytouch delete 2
systemctl --user status tinytouch-helper.service
journalctl --user -u tinytouch-helper.service -n 50
systemctl --user restart tinytouch-helper.service
```

`--port /dev/ttyACM0` can go before or after device commands. Foreground commands
share the existing lease mechanism, so the helper drains its USB workers before
they open the port and reconnects afterward to reload credentials.

An app query failure falls back without blocking password typing. Test with a
non-sensitive throwaway password in a plain text editor before saving real app
passwords. Verify the lock-screen fallback separately. Hardware typing and lock
screen behavior must be tested locally; unit tests do not replace that check.

To disable the port:

```sh
systemctl --user disable --now tinytouch-helper.service
```

This leaves saved credentials intact. Removing the installed source directory
also leaves the wallet items and replay settings intact.

## Development and validation

```sh
python3 -m venv .venv-linux
.venv-linux/bin/python -m pip install -r linux/requirements.txt
.venv-linux/bin/python -m unittest discover -s tests/linux -v
PYTHONPATH=tests .venv-linux/bin/python -m unittest \
  test_helper_protocol test_helper_runtime test_helper_recovery \
  test_password_mapping test_serial_latency test_lease_cleanup test_cli_transport
.venv-linux/bin/python linux/cli.py app
```

The existing full macOS test suite still runs on macOS. Linux-specific tests
cover desktop timeout/lock/spoofing behavior, secret-store failures, CLI password
updates, setup, and user-service handoff. No tests require real credentials.

### Hardware validation

Setup, fingerprint AUTH, and HID password typing were confirmed on Bazzite
with KDE/Wayland and firmware 0.1.35 after installing `99-tinytouch.rules`.
The earlier `70-` rule was overridden by the distribution's smart-card power
rule, causing spontaneous USB reconnects and AUTH timeouts. The later rule
resolved that interruption in this test.

Live KWin app lookup was also exercised. Lock-state handling is covered by
mocked tests; lock-screen password delivery still needs hardware validation.
