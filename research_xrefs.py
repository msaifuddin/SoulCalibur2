#!/usr/bin/env python3
"""Locate likely MIPS references to strings in the unpacked SC2 image."""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

from capstone import CS_ARCH_MIPS, CS_MODE_LITTLE_ENDIAN, CS_MODE_MIPS64, Cs


# The first four bytes of the decrypted stream are 0x00100000. The loader
# skips that word and expands this image at that EE address.
BASE = 0x00100000


def words(data: bytes):
    for offset in range(0, len(data) - 3, 4):
        yield offset, struct.unpack_from("<I", data, offset)[0]


def refs_to(data: bytes, target: int):
    target_hi_adjusted = ((target + 0x8000) >> 16) & 0xFFFF
    target_hi_plain = (target >> 16) & 0xFFFF
    target_lo = target & 0xFFFF
    all_words = list(words(data))
    hits = []
    for idx, (offset, word) in enumerate(all_words):
        if word >> 26 != 0x0F:  # LUI
            continue
        rt = (word >> 16) & 0x1F
        imm = word & 0xFFFF
        if imm not in (target_hi_adjusted, target_hi_plain):
            continue
        for next_offset, next_word in all_words[idx + 1 : idx + 9]:
            opcode = next_word >> 26
            rs = (next_word >> 21) & 0x1F
            next_rt = (next_word >> 16) & 0x1F
            next_imm = next_word & 0xFFFF
            if rs != rt or next_rt != rt:
                continue
            if opcode == 0x09:  # ADDIU, signed low half
                formed = ((imm << 16) + struct.unpack("<h", struct.pack("<H", next_imm))[0]) & 0xFFFFFFFF
            elif opcode == 0x0D:  # ORI
                formed = (imm << 16) | next_imm
            else:
                continue
            if formed == target:
                hits.append((offset, next_offset))
    return hits


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("text", nargs="+")
    args = parser.parse_args()
    data = args.image.read_bytes()
    md = Cs(CS_ARCH_MIPS, CS_MODE_MIPS64 | CS_MODE_LITTLE_ENDIAN)
    for text in args.text:
        needle = text.encode("ascii")
        start = 0
        while True:
            string_offset = data.find(needle, start)
            if string_offset < 0:
                break
            target = BASE + string_offset
            hits = refs_to(data, target)
            print(f"{text!r} file={string_offset:#x} runtime={target:#x} refs={hits}")
            for first, _ in hits:
                begin = max(0, first - 0x30)
                end = min(len(data), first + 0x50)
                for insn in md.disasm(data[begin:end], BASE + begin):
                    marker = ">" if insn.address == BASE + first else " "
                    print(f" {marker} {insn.address:08x}: {insn.mnemonic:8s} {insn.op_str}")
            start = string_offset + len(needle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
