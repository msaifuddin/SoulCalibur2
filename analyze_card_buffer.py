#!/usr/bin/env python3
"""Compare raw Conquest-card data pages with SC2's EE-side card buffer."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path


RAW_PAGE = 528
DATA_PAGE = 512


def dump(start: int, block: bytes) -> None:
    for row in range(0, len(block), 16):
        part = block[row : row + 16]
        hexed = " ".join(f"{byte:02x}" for byte in part)
        text = "".join(chr(byte) if 32 <= byte < 127 else "." for byte in part)
        print(f"{start + row:08x}  {hexed:<47}  {text}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("card", type=Path)
    parser.add_argument("ram", type=Path)
    parser.add_argument("--base", type=lambda x: int(x, 0), default=0x3C5354)
    parser.add_argument("--pages", type=lambda x: int(x, 0), default=0x800)
    parser.add_argument("--show-page", action="append", type=lambda x: int(x, 0))
    args = parser.parse_args()

    raw = args.card.read_bytes()
    card = b"".join(
        raw[offset : offset + DATA_PAGE] for offset in range(0, len(raw), RAW_PAGE)
    )
    ram = args.ram.read_bytes()
    compared = min(args.pages, len(card) // DATA_PAGE)
    same = []
    changed = []
    for page in range(compared):
        disk = card[page * DATA_PAGE : (page + 1) * DATA_PAGE]
        live = ram[
            args.base + page * DATA_PAGE : args.base + (page + 1) * DATA_PAGE
        ]
        (same if disk == live else changed).append(page)
    print(f"base={args.base:#x} pages={compared:#x} same={len(same)} changed={len(changed)}")
    print("same(first 80): " + ", ".join(f"{x:#x}" for x in same[:80]))
    print("changed(first 80): " + ", ".join(f"{x:#x}" for x in changed[:80]))

    for page in args.show_page or []:
        disk = card[page * DATA_PAGE : (page + 1) * DATA_PAGE]
        live_start = args.base + page * DATA_PAGE
        live = ram[live_start : live_start + DATA_PAGE]
        xor = bytes(a ^ b for a, b in zip(disk, live))
        print(f"\npage {page:#x} card:")
        dump(page * DATA_PAGE, disk[:128])
        print(f"page {page:#x} EE @ {live_start:#x}:")
        dump(live_start, live[:128])
        common = Counter(xor).most_common(8)
        print("xor common: " + ", ".join(f"{value:#04x}:{count}" for value, count in common))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
