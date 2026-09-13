#!/usr/bin/env python3
"""Locate SC2 guest structures inside a PCSX2 host-memory-region dump."""

from __future__ import annotations

import argparse
import mmap
from pathlib import Path


NEEDLES = {
    "card_magic": b"Memory Card for SoulCaliburII (C)1995 1998 2002 NAMCO LTD.",
    "ps2_format": b"Sony PS2 Memory Card Format",
    "conquest": b"CONQUEST",
    "password": b"PASSWORD",
    "entry_code": bytes.fromhex(
        "7e00043c0000053c44ce8424ffffa5243d0003240c0000002eee090c00000000"
    ),
}


def occurrences(data: mmap.mmap, needle: bytes):
    at = 0
    while True:
        at = data.find(needle, at)
        if at < 0:
            return
        yield at
        at += 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dump", type=Path)
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--slice", dest="slices", action="append", type=lambda x: int(x, 0))
    parser.add_argument("--slice-size", type=lambda x: int(x, 0), default=0x400)
    parser.add_argument("--stats", action="store_true")
    parser.add_argument("--needle-file", type=Path)
    parser.add_argument("--needle-offset", type=lambda x: int(x, 0), default=0)
    parser.add_argument("--needle-size", type=lambda x: int(x, 0), default=32)
    parser.add_argument("--search", action="append")
    parser.add_argument("--card", type=Path)
    parser.add_argument("--card-pages", default="0,1,0x10,0x20,0x21,0x3f,0x70,0x100,0x200,0x400,0x600,0x7ff")
    args = parser.parse_args()

    with args.dump.open("rb") as stream, mmap.mmap(
        stream.fileno(), 0, access=mmap.ACCESS_READ
    ) as data:
        print(f"size={len(data):#x}")
        if args.stats:
            nonzero = sum(byte != 0 for byte in data)
            print(f"nonzero={nonzero:#x} ({nonzero / len(data):.2%})")
        if args.slices:
            for start in args.slices:
                block = data[start : start + args.slice_size]
                print(f"slice {start:#x}..{start + len(block):#x}")
                for row in range(0, len(block), 16):
                    part = block[row : row + 16]
                    hexed = " ".join(f"{byte:02x}" for byte in part)
                    ascii_text = "".join(chr(byte) if 32 <= byte < 127 else "." for byte in part)
                    print(f"{start + row:08x}  {hexed:<47}  {ascii_text}")
            return 0
        if args.needle_file:
            source = args.needle_file.read_bytes()
            needle = source[args.needle_offset : args.needle_offset + args.needle_size]
            hits = list(occurrences(data, needle))
            print(f"file_needle={needle.hex()}")
            print("hits=" + ", ".join(f"{x:#x}" for x in hits))
            return 0
        if args.search:
            for value in args.search:
                hits = list(occurrences(data, value.encode("ascii")))
                print(f"{value}: " + ", ".join(f"{x:#x}" for x in hits))
            return 0
        if args.card:
            card = args.card.read_bytes()
            for text_page in args.card_pages.split(","):
                page = int(text_page, 0)
                raw = card[page * 528 : page * 528 + 512]
                for start in (0, 16, 64, 256):
                    needle = raw[start : start + 32]
                    if len(set(needle)) < 5:
                        continue
                    hits = list(occurrences(data, needle))
                    if hits:
                        print(
                            f"page={page:#x} data+{start:#x}: "
                            + ", ".join(f"{x:#x}" for x in hits)
                        )
            return 0
        for label, needle in NEEDLES.items():
            hits = list(occurrences(data, needle))
            shown = ", ".join(f"{x:#x}" for x in hits[: args.limit])
            suffix = " ..." if len(hits) > args.limit else ""
            print(f"{label}: {len(hits)} hit(s): {shown}{suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
