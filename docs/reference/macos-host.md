# macOS host architecture

The macOS host software supports tinyTouch firmware protocol version 5. It does
not change the device wire protocol.

## Service lifecycle

The HID helper is a per-user launch agent. `launchd` keeps the job registered
and restarts it after an unexpected exit. Foreground CLI commands do not unload
the job or terminate its process.

The CLI and helper coordinate through an exclusive lease:

1. The CLI takes a kernel file lock and writes a private lease record.
2. The helper stops its serial workers and acknowledges the lease nonce.
3. The CLI opens the serial device after it receives the acknowledgement.
4. The CLI removes the lease when the command ends.
5. If the CLI is interrupted, the kernel releases the lock. The helper detects
   the dead owner, removes the stale lease, and resumes.

The helper manages one worker for each USB serial number. A worker does not own
a `/dev/cu.*` name permanently. It reconnects when macOS assigns a new port name
to the same device.

Serial and Keychain failures use bounded exponential backoff with jitter. The
maximum delay is 30 seconds. A failure does not block a device for the remaining
life of the helper process.

The launch agent uses these lifecycle controls:

- `KeepAlive` keeps the manager available.
- `ThrottleInterval` prevents a crash loop from consuming resources.
- `ProcessType=Background` identifies the job as background work.
- `ExitTimeOut` bounds shutdown.

See Apple's [launchd job guidance](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html)
and the `launchd.plist(5)` manual page on the installed macOS version.

## Device discovery and sleep recovery

The helper accepts a runtime interface only when all of these values match:

| Property | Required value |
| --- | --- |
| USB vendor ID | `0x303a` |
| USB product ID | `0x4001` |
| Serial number | `TT-` followed by 12 hexadecimal characters |

The serial number is the durable identity. The BSD port name and USB location
are observations that can change after reconnect, wake, or re-enumeration.

The helper sends `PING` after five idle seconds. It requires `PONG` within two
seconds. It also compares the open port with the current device inventory. A
failed heartbeat or missing port closes the descriptor and returns ownership to
the manager.

macOS provides sleep and wake notifications, but correctness does not depend on
receiving one. See Apple's
[`NSWorkspace` environment notifications](https://developer.apple.com/documentation/appkit/nsworkspace).
The helper recovers from the observable USB state, including missed notifications
and dark wake.

## Serial framing and protocol compatibility

The host uses the protocol 6 event messages:

- Device to host: `EV` and `EV2`.
- Host to device: `PW` and `PW2`.
- Liveness: `PING` and `PONG`.

The decoder accepts newline-delimited ASCII frames up to 2,048 bytes. It keeps a
split frame until the newline arrives. If a frame exceeds the limit, the decoder
discards all remaining bytes through the next newline. This prevents an
oversized frame tail from becoming a separate authenticated event.

The parser validates the exact field count, numeric ranges, nonce size,
authenticator count, authenticator syntax, and HMAC before it selects a
password. `EV2` accepts up to eight distinct computer authenticators, which
matches the firmware host-key capacity.

The host records an event nonce after the encrypted response is written and
flushed. This favors delivery after transient USB failure. Protocol 5 has no
response acknowledgement, so it cannot provide exactly-once delivery across a
host crash after the USB write. A future acknowledgement must be an optional,
backward-compatible extension.

## Keychain behavior

Passwords and pairing keys remain in the current user's login Keychain. The
helper disables Keychain user-interface requests before it starts workers. A
locked Keychain, dark wake, or required interaction therefore returns a status
code instead of displaying an unattended prompt. The manager logs the status
and retries.

The CLI remains interactive. Setup and password replacement can show a Keychain
access prompt when macOS requires one.

Legacy Keychain items use the file-based Keychain access-control model because
the distributed command-line tool has no app provisioning profile. Apple
recommends the `SecItem` API and documents the distinction in
[TN3137](https://developer.apple.com/documentation/technotes/tn3137-on-mac-keychains).
The current compatibility layer retains existing items and ACLs. A future app
bundle can migrate to the data-protection Keychain after it has a stable signing
identity and access group.

Relevant Apple references include
[Keychain result codes](https://developer.apple.com/documentation/security/security-framework-result-codes),
[Keychain access-control lists](https://developer.apple.com/documentation/security/access-control-lists),
and [`SecAccessCreate`](https://developer.apple.com/documentation/security/secaccesscreate%28_%3A_%3A_%3A%29).

## Keyboard layouts and password recovery

`keyboard-layout auto` obtains the current ASCII-capable macOS layout and maps
each password character to the physical key that the firmware's US HID table
will send. The helper rejects a character that the active layout cannot produce
with one non-dead key. It does not guess or send a partial password.

`keyboard-layout us` sends the stored ASCII password without translation. Use it
only when the target password field uses a US layout.

Replace an incorrect stored password without erasing fingerprints or device
keys:

```sh
tinytouch password set
tinytouch keyboard-layout auto
tinytouch test
```

The USB-IF defines keyboard usages as physical controls, not characters. The
operating-system layout determines the produced character. See the
[USB HID specification](https://www.usb.org/sites/default/files/hid1_12.pdf)
and [HID Usage Tables](https://www.usb.org/documents?search=hid).

## PIV and CryptoTokenKit

PIV mode is handled by macOS, not the HID helper. The firmware exposes CCID only
in PIV mode. HID mode omits CCID so CryptoTokenKit does not discover an unpaired
smart card and show pairing notifications.

The CLI uses `sc_auth identities` to wait for macOS discovery and `sc_auth pair`
to associate the PIV authentication identity with the local account. Apple
documents that pairing also changes the user's Keychain so it can be unlocked
with the card. The installed `sc_auth(8)` and `SmartCardServices(7)` manual pages
are authoritative for the running macOS version.

Slot behavior must remain:

- Slot `9a` authenticates login, screen saver, `sudo`, and other PAM operations.
- Slot `9d` unlocks the login Keychain and participates in Apple silicon login.
- Both private-key operations require a fresh fingerprint authorization.

See Apple's [smart-card deployment guide](https://support.apple.com/guide/deployment/use-a-smart-card-on-mac-depc705651a9/web),
[FileVault smart-card guidance](https://support.apple.com/guide/deployment/filevault-and-smart-card-usage-dep806850525/web),
and [CryptoTokenKit slot manager](https://developer.apple.com/documentation/cryptotokenkit/tksmartcardslotmanager).
The slot-manager API requires the smart-card entitlement, so the CLI uses the
system `sc_auth` interface instead.

## Installation, upgrade, and removal

The installer downloads a release manifest and architecture-specific archive
and checks its SHA-256 digest before it changes the command link. The current
pre-production build keeps its existing ad hoc or local-development signing
path. It does not require an Apple Developer membership, Developer ID,
notarization, or Gatekeeper assessment.

Developer ID distribution remains an optional future release step. See Apple's
[notarization requirements](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)
and [distribution-signing guidance](https://developer.apple.com/documentation/xcode/creating-distribution-signed-code-for-the-mac)
if that distribution model is adopted later.

Upgrade verifies the release archive before replacing the CLI. Device state is
stored on the device and is not changed by a CLI upgrade.

Run this command to remove executable service components:

```sh
tinytouch uninstall
```

Uninstall preserves Keychain credentials, settings, and nonce history. This
allows a host reinstall without erasing device state.

## Diagnostics

Helper logs use one JSON object per line. Event names include:

- `manager.started`, `manager.transition`, `manager.suspended`, and `manager.resumed`
- `worker.started`, `worker.connected`, `worker.failed`, and `worker.drained`
- `keychain.unavailable`
- `protocol.frame_rejected`, `protocol.event_rejected`, and
  `protocol.password_delivered`

Create a private support snapshot:

```sh
tinytouch diagnostics --output ~/Desktop/tinytouch-diagnostics.json
```

The snapshot includes service state, detected USB metadata, and recent
structured events. It does not read or export Keychain secrets.

## Firmware rewrite integration contract

The firmware rewrite must meet these host requirements:

1. Keep `protocol=6` and the event and terminal-response syntax described above.
2. Keep the runtime USB identity at VID `0x303a`, PID `0x4001`, with a stable
   `TT-XXXXXXXXXXXX` serial derived from the device identity.
3. Expose CDC in both modes. Expose HID in HID mode. Expose CCID and the PIV PIN
   HID path only in PIV mode.
4. Complete USB re-enumeration after `SET MODE` when descriptors change. Preserve the USB
   serial across the new BSD port name.
5. Reply to `PING` with one complete `PONG\n` frame while CDC is healthy.
6. Emit each `EV` or `EV2` as one newline-terminated frame. Never reuse a
   16-byte event nonce. Include no more than eight `EV2` authenticators.
7. Continue accepting `PW` and `PW2` exactly as defined above. Wipe
   decrypted password bytes after HID delivery.
8. Restore CDC, HID, and CCID endpoint state after resume. A touch during
   suspend may request remote wake, but must not run PIV or password delivery
   before USB resume is complete.
9. Keep CCID slot-change notifications accurate after resume and
   re-enumeration. Follow the USB-IF
   [CCID 1.1 specification](https://www.usb.org/document-library/smart-card-ccid-version-11).
10. Keep slots `9a` and `9d` fingerprint-gated. One login touch may authorize the
    immediate `9a` operation and its related `9d` Keychain-unlock operation. Do
    not leave either slot generally authorized.
11. Preserve NVS host keys, fingerprints, mode, and typing settings across
    firmware updates and USB re-enumeration.
12. If firmware adds delivery acknowledgement, negotiate it with a new status
    capability. Hosts must reject a device that does not report protocol 6.

Run host failure-injection tests with split frames, oversized frames, stale
leases, CLI termination, Keychain interaction denial, port renumbering,
disconnect during response write, sleep/wake, and repeated mode switching.
