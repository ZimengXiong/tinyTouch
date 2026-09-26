#!/usr/bin/env python3
"""Compare helper serial framing on a local pseudo terminal, without hardware."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time

import serial

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "macos"))
from tinytouch_runtime import SerialFrameDecoder, read_available


def measure(payload: bytes, trials: int, legacy: bool) -> dict[str, float]:
    master, slave = os.openpty()
    samples = []
    try:
        with serial.Serial(os.ttyname(slave), timeout=0.2) as connection:
            for _ in range(trials):
                decoder = SerialFrameDecoder(2048)
                started = time.perf_counter_ns()
                os.write(master, payload)
                while True:
                    chunk = connection.read(256) if legacy else read_available(connection)
                    if decoder.feed(chunk):
                        break
                    if legacy:
                        time.sleep(0.01)
                    if time.perf_counter_ns() - started > 2_000_000_000:
                        raise RuntimeError("pseudo terminal stopped responding")
                samples.append((time.perf_counter_ns() - started) / 1_000_000)
    finally:
        os.close(master)
        os.close(slave)
    samples.sort()
    return {
        "p50_ms": round(statistics.median(samples), 3),
        "p95_ms": round(samples[math.ceil(len(samples) * 0.95) - 1], 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials", type=int, default=20)
    args = parser.parse_args()
    if args.trials < 1:
        parser.error("--trials must be positive")
    if not hasattr(os, "openpty"):
        parser.error("requires POSIX pseudo terminals")
    nonce, mac = "12" * 16, "34" * 32
    frames = {
        "single_host": f"EV {nonce} 1 1 99 {mac}\r\n".encode(),
        "eight_hosts": (f"EV2 {nonce} 1 1 99 " + " ".join(
            f"{i:016x}:{mac}" for i in range(8)
        ) + "\r\n").encode(),
    }
    results = {"trials": args.trials, "scope": "PTY framing only; synthetic events, no authentication or hardware"}
    for name, payload in frames.items():
        results[name] = {
            "bytes": len(payload),
            "before": measure(payload, args.trials, True),
            "after": measure(payload, args.trials, False),
        }
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
