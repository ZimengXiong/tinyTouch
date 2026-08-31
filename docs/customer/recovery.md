# Recovery

Recovery erases fingerprints, keys, pairings, settings, and firmware state.

Try this first:

```sh
tinytouch status --verbose
tinytouch factory-reset
```

If the device still cannot be set up, open the [Flash center](/flash), choose **Recovery firmware**, and follow the steps. You need Chrome or Edge, a USB data cable, and access to **BOOT** and **RESET**.

After recovery, unplug and reconnect the device, wait 20 seconds, then run:

```sh
tinytouch setup
```

See the [Recovery reference](/reference/recovery) for details.
