"""Generate the neutral studio environment used by the case explorer."""

import math
from pathlib import Path


def build_environment():
    width, height = 512, 256
    # Direction, intensity, angular size: key, fill, and rim softboxes.
    lights = [
        ((-0.6, 0.7, 0.5), 9.0, 0.28),
        ((0.8, 0.25, 0.5), 1.5, 0.4),
        ((0.0, 0.5, -0.9), 4.0, 0.22),
    ]
    lights = [
        (tuple(v / math.sqrt(sum(c * c for c in direction)) for v in direction), intensity, size)
        for direction, intensity, size in lights
    ]
    pixels = bytearray()
    for y in range(height):
        theta = math.pi * (y + 0.5) / height
        for x in range(width):
            phi = 2 * math.pi * (x + 0.5) / width
            direction = (math.sin(theta) * math.cos(phi), math.cos(theta), math.sin(theta) * math.sin(phi))
            radiance = 0.07
            for light, intensity, size in lights:
                angle = math.acos(max(-1, min(1, sum(a * b for a, b in zip(direction, light)))))
                radiance += intensity * math.exp(-0.5 * (angle / size) ** 4)
            mantissa, exponent = math.frexp(radiance)
            channel = min(255, int(mantissa * 256))
            pixels.extend((channel, channel, channel, exponent + 128))
    target = Path(__file__).resolve().parents[1] / "public" / "case-studio.hdr"
    header = f"#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n-Y {height} +X {width}\n"
    target.write_bytes(header.encode("ascii") + pixels)
    print(target)


if __name__ == "__main__":
    build_environment()
