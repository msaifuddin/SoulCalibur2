#!/usr/bin/env python3
"""Search an SC2 binary image for common cryptographic constants."""

from __future__ import annotations

import argparse
from pathlib import Path


PATTERNS = {
    "AES S-box": bytes.fromhex("637c777bf26b6fc53001672bfed7ab76"),
    "AES inverse S-box": bytes.fromhex("52096ad53036a538bf40a39e81f3d7fb"),
    "TEA delta LE": bytes.fromhex("b979379e"),
    "TEA delta BE": bytes.fromhex("9e3779b9"),
    "SHA-1 constants LE": bytes.fromhex("9982f0d9a19148a1dcbc1b8ff6cddbe5"),
    "MD5 constants LE": bytes.fromhex("78a46ad756b7c7e8db702024eecebdc1"),
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("binary", type=Path)
    args = parser.parse_args()
    data = args.binary.read_bytes()
    for label, pattern in PATTERNS.items():
        hits = []
        at = 0
        while True:
            at = data.find(pattern, at)
            if at < 0:
                break
            hits.append(at)
            at += 1
        print(f"{label}: " + (", ".join(f"{x:#x}" for x in hits) or "none"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
