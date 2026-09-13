#!/usr/bin/env python3
"""Find MIPS references to an EE address inside a flat 32 MiB EE RAM image.

The image is the live program, so a file offset is already the EE address.
That avoids having to reconstruct how the PS2AC05 segments were relocated.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import struct

from capstone import CS_ARCH_MIPS, CS_MODE_LITTLE_ENDIAN, CS_MODE_MIPS64, Cs


LUI = 0x0F
ADDIU = 0x09
ORI = 0x0D
LOAD_STORE = {
    0x20: "lb", 0x21: "lh", 0x23: "lw", 0x24: "lbu", 0x25: "lhu", 0x27: "lwu",
    0x28: "sb", 0x29: "sh", 0x2B: "sw", 0x37: "ld", 0x3F: "sd",
}


def signed(value: int) -> int:
    return value - 0x10000 if value & 0x8000 else value


def find_refs(data: bytes, target: int, start: int, end: int, window: int = 12):
    """Yield (lui_address, pair_address, formed_address) for hi/lo pairs."""
    hi_adjusted = ((target + 0x8000) >> 16) & 0xFFFF
    hi_plain = (target >> 16) & 0xFFFF
    for address in range(start, end - 4, 4):
        word = struct.unpack_from("<I", data, address)[0]
        if word >> 26 != LUI:
            continue
        immediate = word & 0xFFFF
        if immediate not in (hi_adjusted, hi_plain):
            continue
        register = (word >> 16) & 0x1F
        for step in range(1, window):
            follow_at = address + step * 4
            if follow_at >= end:
                break
            follow = struct.unpack_from("<I", data, follow_at)[0]
            opcode = follow >> 26
            base = (follow >> 21) & 0x1F
            if base != register:
                continue
            low = follow & 0xFFFF
            if opcode == ADDIU and ((follow >> 16) & 0x1F) == register:
                formed = ((immediate << 16) + signed(low)) & 0xFFFFFFFF
            elif opcode == ORI and ((follow >> 16) & 0x1F) == register:
                formed = (immediate << 16) | low
            elif opcode in LOAD_STORE:
                formed = ((immediate << 16) + signed(low)) & 0xFFFFFFFF
            else:
                # The register was reloaded before any low half was applied.
                if opcode == LUI and ((follow >> 16) & 0x1F) == register:
                    break
                continue
            yield address, follow_at, formed
            break


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path, help="flat EE RAM dump")
    parser.add_argument("address", type=lambda x: int(x, 0))
    parser.add_argument("--span", type=lambda x: int(x, 0), default=0x400,
                        help="treat references inside address..address+span as hits")
    parser.add_argument("--start", type=lambda x: int(x, 0), default=0x100000)
    parser.add_argument("--end", type=lambda x: int(x, 0), default=0x800000)
    parser.add_argument("--context", type=lambda x: int(x, 0), default=0x20)
    parser.add_argument("--list-only", action="store_true")
    args = parser.parse_args()

    data = args.image.read_bytes()
    engine = Cs(CS_ARCH_MIPS, CS_MODE_MIPS64 | CS_MODE_LITTLE_ENDIAN)
    hits = [
        hit
        for hit in find_refs(data, args.address, args.start, args.end)
        if args.address <= hit[2] < args.address + args.span
    ]
    print(f"{len(hits)} reference(s) to {args.address:#x}..{args.address + args.span:#x}")
    for lui_at, pair_at, formed in hits:
        print(f"  {lui_at:#08x} -> {formed:#08x}")
        if args.list_only:
            continue
        begin = max(0, lui_at - args.context)
        finish = pair_at + args.context
        for insn in engine.disasm(data[begin:finish], begin):
            marker = ">" if insn.address in (lui_at, pair_at) else " "
            print(f"   {marker} {insn.address:08x}: {insn.mnemonic:8s} {insn.op_str}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
