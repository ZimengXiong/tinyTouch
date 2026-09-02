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

PIV authentication of lockscreen (you know its PIV because it says PIN and not password in the entry field) (the typing is just the PIV PIN, which we bypass (since we gate by the fingerprint), read below to learn more about it)

https://github.com/user-attachments/assets/88014cb2-34d2-4d63-8998-54f0561364eb

if you would like to support this project, please consider [donating](https://github.com/sponsors/ZimengXiong) or contributing!


## table of contents

- [red pill or blue pill?](#red-pill-or-blue-pill)
- [install](#install)
  - [if you have a device](#if-you-have-a-device)
  - [if the device has no firmware yet](#if-the-device-has-no-firmware-yet)
  - [building the firmware yourself](#building-the-firmware-yourself)
  - [checking a piv setup](#checking-a-piv-setup)
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
a dummy pin. that pin is not your mac password. it is just there to get through
the macos piv prompt while the real authorization is the fingerprint gate around
the piv key. it is typed using numeric-keypad usages, which national keyboard
layouts do not remap, so it arrives as digits whatever layout is active.

this avoids typing your real password, but only works where macos accepts smart
cards, like login and `sudo` with pam.

## install

the mac side is the `tinytouch` cli. it selects the mode, provisions the piv
keys, enrolls fingerprints, stores the hid password in your keychain, and
installs the background helper. both modes run from the same firmware, so
switching between them does not need a reflash.

### if you have a device

```sh
curl -fsSL https://docs.tinytouch.dev/install.sh | sh
tinytouch setup
```

`tinytouch setup` asks which mode you want and walks through the rest. add
`--mode hid` or `--mode piv` to skip the menu.

to switch modes later:

```sh
tinytouch mode piv
```

full instructions, including recovery, are at
[docs.tinytouch.dev](https://docs.tinytouch.dev).

### if the device has no firmware yet

flash the factory image from the browser at
[docs.tinytouch.dev/flash](https://docs.tinytouch.dev/flash). hold the boot
button while plugging in the usb cable to enter bootloader mode. then run
`tinytouch setup` as above.

### building the firmware yourself

the firmware needs **esp-idf 5.3.x**. it does not build on 6.0: idf 6.0 ships
mbedtls 4.x, which moved the legacy rsa api that piv slots depend on, and its
`driver` component no longer re-exports `driver/uart.h`. the build stops at
configure time with a message saying so rather than failing halfway through
compilation.

```sh
./firmware/build-and-flash --port /dev/cu.usbmodem101
```

pass `--build-only` to build without flashing. after flashing, run `tinytouch
setup`.

if a build against 6.0 already failed, delete `firmware/tiny_touch_unified/build`,
`sdkconfig`, and `dependencies.lock` before rebuilding. the lockfile records the
idf version it resolved against and will keep the build broken otherwise.

there is no `secrets.h` step any more. the piv certificates and private keys for
slots `9a` and `9d` are generated by `tinytouch setup` and stored in the device's
nvs, not compiled into the firmware image.

### checking a piv setup

```sh
system_profiler SPSmartCardsDataType
sc_auth identities
```

`tinytouch setup` pairs the identity for you. to test sudo:

```sh
sudo -k
sudo -v
```

when macos asks for the pin, touch the sensor. the pin prompt is expected: the
firmware types a dummy pin, and the fingerprint is the real gate.

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

the fingerprint sensor connects over uart to pins 6 and 7 for tx and rx.

the interrupt pin can be connected anywhere. in firmware, it is connected to pin
1.

## notes

the firmware used to live in two directories, `tiny_touch_keyboard` (an arduino
sketch for hid) and `tiny_touch_smartcard` (an idf project for piv). both were
replaced by the single idf project in `firmware/tiny_touch_unified`, which serves
both modes. if you are following an older guide or video that mentions
`tiny_touch_keyboard.ino`, that file no longer exists and the mode is now a
runtime setting.

[cad](https://cad.onshape.com/documents/d0e6bb7977e6171d4e4a5086/w/1ded27ad6c634fd1fdaf26d0/e/aca67210e400490a08d0b29a?renderMode=0&uiState=6a4c1df32e292f12144a65fe). if you make changes, please make them open source as well.

## bonus images

<img width="2261" height="1347" alt="render2" src="https://github.com/user-attachments/assets/5f107d74-d651-4e3b-90ed-f37dcaa026ac" />
<img width="1238" height="901" alt="cross" src="https://github.com/user-attachments/assets/6a7062d9-ec56-4aac-adad-00d888e7d486" />
<img width="1280" height="957" alt="tinyTouch" src="https://github.com/user-attachments/assets/ad66c9b3-5823-44d3-bd73-bba64f2e60ab" />
