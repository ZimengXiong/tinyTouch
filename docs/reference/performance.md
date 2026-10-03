---
title: Unlock latency
description: Measure fingerprint capture, helper response, and password typing separately.
---

# Unlock latency

Firmware and helper 0.1.34-dev.1 remove several waits between touching the sensor and
typing a password. Install both parts to get the full improvement. No fingerprint
thresholds, authentication checks, replay protections, or lift requirements change.

## Changes

| Stage | Previous behavior | Current behavior |
|---|---|---|
| Detect touch | Check the GPIO every 10 ms | A GPIO edge wakes the task; the level and lift state still gate capture |
| Read sensor reply | Wait to fill a 96-byte buffer, even for a short ACK | Wait for one byte, then drain available data; deliver short packets after two idle UART symbols |
| Show match result | Finish the LED command and wait 350 ms before proceeding | Show and clear the result in a separate task while authentication continues |
| Read helper request | `read(256)` can delay a complete short frame until the 200 ms timeout | Read available bytes immediately; block for one byte when idle |
| Process larger requests | Sleep 10 ms between received batches | Process buffered batches consecutively |
| Translate keyboard layout | Rebuild the map on every touch | Cache translations by layout contents, checking the current layout every time |
| Receive helper reply | Poll CDC input every 10 ms | Wake the console when CDC data arrives |
| Type password | USB poll interval and readiness polling both 10 ms | Request 1 ms USB polling and advance on completed reports |

The CDC transmit buffer also holds a complete multi-host request, reducing waits
while it is queued. LED feedback still returns to the configured idle state after
the configured feedback duration (350 ms by default) even when the helper is absent. Foreground enrollment cancels older feedback.

## Typing speed

New configurations default to a **1 ms minimum after each key press and release**.
Existing configurations keep their stored value; select the faster setting with:

```sh
tinytouch config typing_delay_ms 1
```

The old 100 Hz scheduler rounded settings below 10 ms down to zero. The current
implementation uses a timer to honor millisecond settings and waits for actual USB
completion, including the final release. Repeated characters retain separate
press/release reports. A transfer failure stops typing and prevents a later Enter;
if the transport is still available, it attempts to release any pressed key.

A 20-character password plus Enter needs 42 reports. With a simulated host completing
each report in 1 ms, the transport takes 42 ms at a 1 ms setting, or 294 ms at 7 ms.
These are model timings, not measured Mac login times. Host scheduling, application
behavior, fingerprint matching and macOS login processing add their own latency.
Increase the delay if a field drops characters.

## Measure on a device

After an ordinary unlock, use `tinytouch logs`. Each
entry has a monotonic millisecond timestamp:

| Difference | What it measures |
|---|---|
| `touch_detected` → `finger_matched` | Sensor capture and matching |
| `finger_matched` → `hid_requested` | Preparing the authenticated request |
| `hid_requested` → `hid_response` | CDC transmission and helper response |
| `hid_response` → `hid_typing` | Queue delivery and response verification |
| `hid_typing` → `hid_typed` | HID delivery and configured pacing, through final release |

`hid_response` means a response arrived; `hid_typing` follows successful verification.
Failures end in `hid_failed`. A legacy multi-host fallback is marked
`hid_legacy_retry`. Logs contain no passwords or key material. The 32-entry ring
holds only recent activity, so retrieve it shortly after a test.

Compare several first unlocks after connection, steady-state unlocks, and wake from
sleep. Check the actual login field as well as logs: completed USB transfers do not
prove the application accepted the text. Use a disposable test password with repeated
letters and shifted characters, test with Enter disabled in a visible field first,
and test both your normal layout and any layout you switch to. Check that a held
finger types only once and a missing helper does not leave the LED green.

## Reproduce the host framing benchmark

From a checkout with `pyserial` installed:

```sh
python3 scripts/benchmark-serial.py --trials 20
```

This uses local pseudo terminals and synthetic EV/EV2 frames; it opens no physical
device and sends no password. It compares the previous read/sleep loop with the
current reader and reports median and 95th-percentile framing times. The request is
queued immediately before reading, which exposes nearly the full old timeout.
Actual touch timing can land anywhere within the old 200 ms read window.

Physical ESP32-S3/sensor and macOS acceptance testing is still required. The host
tests cover framing, authenticated replies, layout cache invalidation, sensor packet
validation, LED expiry, report pacing, repeated keys, disconnects and late USB
completions. They cannot measure the sensor's recognition time or macOS's unlock time.
