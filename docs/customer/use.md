# Use

For PIV, start macOS login or `sudo -v`. At the PIN prompt, touch an enrolled finger. tinyTouch types `111111`.

For HID, focus the password field and touch an enrolled finger. Keep focus unchanged until the password is entered.

If macOS shows a password field during PIV use:

```sh
tinytouch pair
```

For a US keyboard layout:

```sh
tinytouch keyboard-layout us
```

## Common commands

```sh
tinytouch status --verbose
tinytouch test
tinytouch enroll
tinytouch delete --slot 3
tinytouch add-computer
tinytouch computers list
tinytouch password set
```

Reset device state without changing firmware:

```sh
tinytouch factory-reset
```

See [CLI commands](/reference/cli) for all commands.

## Update

```sh
tinytouch update
```

Touch an enrolled finger and keep the device connected until the command finishes. The update preserves device state.

Use [Recovery](./recovery) only when a normal reset cannot solve the problem.
