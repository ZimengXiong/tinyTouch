## interested in preassembled versions? pre order now:
[tinytouch.dev](https://tinytouch.dev)

<img width="2304" height="1152" alt="tinyTouch (4)" src="https://github.com/user-attachments/assets/ec66ec7d-3e14-4292-8085-15374e349057" />

# tinytouch
authenticate, sudo, and log in with your fingerprint wire(less)ly without having
to spend $149.

build guide: https://www.youtube.com/watch?v=YsP1hRg28Gw

https://github.com/user-attachments/assets/efede271-6d84-441d-919c-f5532f687c4e

PIV authentication of sudo:

https://github.com/user-attachments/assets/c197dd9c-81e5-4150-9793-d2e445651dfd

PIV authentication at the lock screen: the field asks for a PIN. tinyTouch types
the PIV PIN `111111` after a fingerprint match; it does not type your Mac password.

https://github.com/user-attachments/assets/88014cb2-34d2-4d63-8998-54f0561364eb

if you would like to support this project, please consider [donating](https://github.com/sponsors/ZimengXiong) or contributing!


## table of contents

- [red pill or blue pill?](#red-pill-or-blue-pill)
- [install](#install)
  - [enroll another finger](#enroll-another-finger)
  - [build from source](#build-from-source)
  - [recover a device](#recover-a-device)
- [hardware](#hardware)
- [wiring](#wiring)
- [notes](#notes)

## red pill or blue pill?

there are two ways to use tinytouch on your computer: `HID` and `PIV/PAM` mode. read about how they work in the sections below.

each has its advantages, and we want to scare you a tiny bit so you actually do
your diligence and understand the security implications of such a device before
you decide whether you are willing to take on the risks:

| features | HID | PIV/PAM* |
| -- | -- | -- |
| keyboardless login | ✅ | ✅ |
| sudo prompts | ✅ | ✅ |
| apple TCC (privacy & security) | ✅ | ✅|
| general settings | ✅ | ❌ |
| keychain/apple passwords | ✅ | ❌ |
| everywhere your password is accepted (remote SSH sessions, etc) | ✅ | depends, but probably not |

| security | HID | PIV/PAM* |
| -- | -- | -- |
| fingerprint sensor <-> esp | 🔴 (unauth'ed UART) | 🔴 (unauth'ed UART) |
| esp <-> computer negotiation | 🟢 (shared-key mac/encryption) | 🔴 (plain usb ccid/apdu) |
| authentication | 🔴 (password typed over hid) | 🟢 (piv challenge/response) |

| attack | HID | PIV/PAM* |
| -- | -- | -- |
| sensor uart spoofing^ | yes | yes |
| wrong focused field | yes | no |
| malicious password field | yes | no |
| usb traffic sniffing | low impact (channel is encrypted/mac'ed) | can observe apdus, not piv private key |
| usb keylogger | can reveal password | cannot reveal key |
| usb command injection | reject bad macs/replays | device may receive apdus, but auth still needs fingerprint-gated key use |
| flash dumping (secure boot/flash encryption off) | shared-key exposable | piv key exposable |
| flash dumping (secure boot/flash encryption on) | shared-key non-exportable | piv key non-exportable |
| flash dumping (with secure element) | shared key non-exportable | piv key non-exportable |

*PIV/PAM always uses HID to deliver the mandatory PIV PIN, which we do not use.
authorization is still gated by your fingerprint. the PIV PIN is not your
password, and is not considered sensitive in our scenario.

^this is the major security issue with this device. since all authentication
happens inside the fingerprint sensor, and the sensor communicates with the esp
over unauthenticated uart, it can be easily spoofed. basic countermeasures
involve filling the insides of the device with black epoxy. a more proper fix
would be upgrading to a more secure fingerprint sensor.

### so... which pill, if any?
this depends on:

1. your security tolerance
2. your environment
3. current/future criminal background
4. family/roommate relations
5. technical skill set of family members/roommates

risks are low to begin with since every attack here requires *physical access* to
both the device and your mac.

so ask yourself: will your device ever leave your desk? can your roommates
perform a flash dump in half an hour? how about your family members? do they have
anything against you that would create a motive? are you wanted by any government
agency? are you protecting sensitive or classified information? are you using a
company device? would you be personally implicated if you leaked company secrets?

if the answer is yes to any of the above questions, i think the magic keyboard presents an excellent value at $149 and is worth the added security.

if the answer is no, chances are you will be fine with a slightly insecure method
of authentication. personally, i am happy with the red pill and love the
convenience of having it work everywhere.

### hid mode

in hid mode, the esp acts like a usb keyboard.

the mac helper keeps your real password encrypted and stored on your mac. this
way, an attacker cannot extract your password from the esp alone. the esp keeps a
shared pairing key. after a fingerprint match, the esp sends a signed request to
the helper, the helper checks it, encrypts the password for that one request, and
sends it back. the esp decrypts it in ram, types it, then wipes it.

this is why it works almost everywhere. it is also why it is scary: the final
step is still your real password being typed into whatever has focus.

to make it less bad, the esp never stores the password. requests use a nonce and
mac so old requests cannot just be replayed, and the helper only sends back an
encrypted one-time response. the password only exists on the esp briefly in ram.

### piv mode

in piv mode, the esp acts like a usb smart card.

macos sends normal piv commands over ccid. when macos needs authentication, it
asks the card to use the piv private key. the esp only allows that key operation
right after a fingerprint match.

macos also expects a piv pin, so the firmware has a tiny hid side path that types
the dummy pin `111111`. that pin is not your mac password. it is just there to
get through the macos piv prompt while the real authorization is the fingerprint
gate around the piv key.

this avoids typing your real password, but only works where macos accepts smart
cards, like login and `sudo` with pam.

## install

HID and PIV use the same firmware. Choose the mode during setup.

1. Build and wire the device using the [hardware guide](https://docs.tinytouch.dev/customer/build).
2. Hold **BOOT** while connecting USB, then release **BOOT**. Open the
   [Flash center](https://docs.tinytouch.dev/flash) in Chrome or Edge and install
   **Factory firmware**.
3. Unplug and reconnect without holding **BOOT**. Install the macOS CLI:

   ```sh
   curl -fsSL https://github.com/ZimengXiong/tinyTouch/releases/latest/download/install.sh | sh
   ```

4. Start setup and follow its prompts:

   ```sh
   tinytouch setup
   ```

Choose HID for password entry or PIV for smart card authentication. Setup enrolls
one finger when the sensor is empty and preserves existing prints. HID setup
saves your password in the Mac's login Keychain and installs its helper. PIV
setup creates the device keys and certificates and pairs the identity with macOS.
If macOS asks for the PIV PIN, enter `111111`.

See the [setup guide](https://docs.tinytouch.dev/customer/setup) and
[CLI reference](https://docs.tinytouch.dev/reference/cli) for current commands.

### enroll another finger

```sh
tinytouch fingers
tinytouch enroll 2
```

Each finger number selects a block with four views of the same finger: left,
right, top, and center. Each view needs two scans. Use an enrolled finger to
approve the command, then use the new finger for every scan. Lift it when prompted.
The sensor supports up to ten fingers when it has 40 template slots.

An occupied block requires replacement confirmation. `tinytouch delete 2`
removes all views in block 2 after fingerprint approval.

### build from source

The firmware project is `firmware/tiny_touch_unified`. It requires **ESP-IDF
5.3.x**, matching CI. Activate that ESP-IDF environment, then run from the
repository root:

```sh
idf.py -C firmware/tiny_touch_unified build
```

The current project uses ESP-IDF and generates PIV keys during setup. The old
Arduino sketch and separate HID/PIV projects are no longer part of this repository.
You do not need `main/secrets.h` for normal setup.

To run the CLI source on macOS, use Python 3.13, which release builds also use:

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install -r macos/requirements.txt
.venv/bin/python macos/cli.py --help
```

For release packaging, see [release/README.md](release/README.md).

### recover a device

If an enrolled finger still works, `tinytouch factory-reset` clears device state
and local pairing after fingerprint approval. If no enrolled finger works, use
**Recovery firmware** in the [Flash center](https://docs.tinytouch.dev/flash?firmware=recovery).
Recovery erases fingerprints, device keys, registered computers, and settings.
Factory flashing alone preserves saved state and does not clear sensor templates.

Follow the [recovery guide](https://docs.tinytouch.dev/customer/recovery), then
run setup again. Browser recovery does not remove Mac Keychain items or smart
card pairings.

## hardware

| part | used here | notes |
| -- | -- | -- |
| microcontroller | seeed studio esp32-s3 | needs native usb and hardware uart. secure boot + flash encryption strongly recommended |
| fingerprint sensor | zw101-style uart sensor | uses the common `0xef01` packet protocol |
| computer | macos | hid mode needs the helper. piv/pam mode needs macos smart card support |
| case | printed top/bottom stl | `hardware/case/case_top.stl` and `hardware/case/case_bottom.stl` |
| wiring/solder/etc | misc | whatever your build needs |

other esp32-s3 boards should work if the usb and uart pins are available. other
fingerprint sensors may work if they speak the same uart protocol. other
microcontroller families can work, but are not currently supported.

## wiring

The firmware uses GPIO43 for UART TX, GPIO44 for UART RX, and GPIO2 for touch
detection. On XIAO ESP32-S3 these are D6, D7, and D1. Connect sensor RX to the
board's TX and sensor TX to the board's RX. Connect both sensor power pins to
3.3 V and its ground pin to GND.

See the [sensor pinout](https://docs.tinytouch.dev/customer/build#_3-wire-it-up)
for connector pin numbers and the Super Mini wiring.

## notes

do not commit:

- `firmware/tiny_touch_unified/main/secrets.h`, if you create it for local work;
- private keys or local signing material such as `secure_boot_signing_key.pem`.

[cad](https://cad.onshape.com/documents/d0e6bb7977e6171d4e4a5086/w/1ded27ad6c634fd1fdaf26d0/e/aca67210e400490a08d0b29a?renderMode=0&uiState=6a4c1df32e292f12144a65fe). if you make changes, please make them open source as well.

## bonus images

<img width="2261" height="1347" alt="render2" src="https://github.com/user-attachments/assets/5f107d74-d651-4e3b-90ed-f37dcaa026ac" />
<img width="1238" height="901" alt="cross" src="https://github.com/user-attachments/assets/6a7062d9-ec56-4aac-adad-00d888e7d486" />
<img width="1280" height="957" alt="tinyTouch" src="https://github.com/user-attachments/assets/ad66c9b3-5823-44d3-bd73-bba64f2e60ab" />
