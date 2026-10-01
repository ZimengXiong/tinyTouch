# PIV touch activation hardware checks

Host simulation validates firmware policy and descriptor contents. Before a
release, run these checks with an ESP32-S3 sensor and a Mac that has no policy
requiring smart-card login. macOS field-switch timing is not observable from USB
enumeration alone; the firmware waits for successful PIV selection plus a
one-second UI settling interval before typing the dummy PIN.

1. Upgrade an existing paired device. Confirm `piv_touch=off`, existing PIV
   login still works, and host keys and fingerprint templates are preserved.
2. Run `tinytouch piv-touch on`, authorize, and reconnect. Confirm status reports
   `piv_touch_active=on piv_visible=no`. Confirm the idle device has HID and CDC
   interfaces and no smart-card interface in the host's USB descriptors.
3. Lock the Mac. Enter the normal password without touching tinyTouch. Confirm
   it unlocks and does not request the dummy smart-card PIN.
4. Lock again and touch an enrolled finger. Confirm the card enumerates, the
   field changes to PIN before any keystrokes, and both login and Login Keychain
   unlock. Check cold connection and wake from sleep, not just repeated logins.
5. Try an unenrolled finger. Confirm there are no typed keys or authorized key
   operations, and the smart-card interface disappears again.
6. Hold an enrolled finger down for over 20 seconds. Confirm only one login
   attempt, the card hides, and password entry returns. Lift and touch to retry.
7. Disable the option, reconnect, and confirm continuous PIV visibility. Switch
   to HID with the option saved on and confirm normal helper authentication.
8. With the option active, pair on a second Mac. Pause over 60 seconds at the
   identity selection or administrator prompt, then continue. Confirm discovery
   renews and fresh fingerprint presence authorizes pairing after any USB reset.
9. Run enrollment and firmware staging while a visibility window expires.
   Confirm replies arrive, serial recovers, and operations are not interrupted.
10. Run `USB RECONNECT` while idle through the console. Confirm it does not
    expose the card. Factory reset and reconnect; confirm `piv_touch=off`.
