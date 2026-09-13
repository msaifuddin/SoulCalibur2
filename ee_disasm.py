#!/usr/bin/env python3
"""Disassemble a range of a flat EE RAM image, resolving hi/lo address pairs."""

from __future__ import annotations

import argparse
from pathlib import Path
import struct

from capstone import CS_ARCH_MIPS, CS_MODE_LITTLE_ENDIAN, CS_MODE_MIPS64, Cs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("address", type=lambda x: int(x, 0))
    parser.add_argument("--size", type=lambda x: int(x, 0), default=0x100)
    parser.add_argument("--until-return", action="store_true",
                        help="stop after the first jr $ra and its delay slot")
    args = parser.parse_args()

    data = args.image.read_bytes()
    engine = Cs(CS_ARCH_MIPS, CS_MODE_MIPS64 | CS_MODE_LITTLE_ENDIAN)
    block = data[args.address : args.address + args.size]
    pending: dict[str, int] = {}
    stop_at = None
    for insn in engine.disasm(block, args.address):
        note = ""
        if insn.mnemonic == "lui":
            register, immediate = [part.strip() for part in insn.op_str.split(",")]
            pending[register] = int(immediate, 0) << 16
        elif insn.mnemonic in ("addiu", "ori", "daddiu"):
            parts = [part.strip() for part in insn.op_str.split(",")]
            if len(parts) == 3 and parts[1] in pending:
                low = int(parts[2], 0)
                if insn.mnemonic == "ori":
                    formed = pending[parts[1]] | (low & 0xFFFF)
                else:
                    formed = (pending[parts[1]] + low) & 0xFFFFFFFF
                note = f"   ; {formed:#x}"
                pending[parts[0]] = formed
                if parts[0] != parts[1]:
                    pending.pop(parts[1], None)
        else:
            base = insn.op_str.rsplit("(", 1)
            if len(base) == 2:
                register = base[1].rstrip(")").strip()
                offset_text = base[0].rsplit(",", 1)[-1].strip()
                if register in pending and offset_text:
                    try:
                        offset = int(offset_text, 0)
                    except ValueError:
                        offset = None
                    if offset is not None:
                        note = f"   ; {(pending[register] + offset) & 0xFFFFFFFF:#x}"
        print(f"{insn.address:08x}: {insn.mnemonic:8s} {insn.op_str}{note}")
        if args.until_return and stop_at is None and insn.mnemonic == "jr" and insn.op_str == "$ra":
            stop_at = insn.address + 8
        if stop_at is not None and insn.address >= stop_at:
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
