#!/usr/bin/env python3
"""Extract selected members from a PCSX2 .p2s ZIP container."""

from __future__ import annotations

import argparse
from pathlib import Path
import struct
import zipfile

import zstandard


def read_member(archive: zipfile.ZipFile, member: str) -> bytes:
    info = archive.getinfo(member)
    try:
        return archive.read(info)
    except NotImplementedError:
        if info.compress_type not in (20, 93):
            raise
        stream = archive.fp
        assert stream is not None
        stream.seek(info.header_offset)
        header = stream.read(30)
        values = struct.unpack("<IHHHHHIIIHH", header)
        if values[0] != 0x04034B50:
            raise ValueError(f"bad local ZIP header for {member}")
        name_size, extra_size = values[-2:]
        stream.seek(name_size + extra_size, 1)
        compressed = stream.read(info.compress_size)
        return zstandard.ZstdDecompressor().decompress(
            compressed, max_output_size=info.file_size
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("state", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("members", nargs="+")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.state) as archive:
        for member in args.members:
            data = read_member(archive, member)
            target = args.output / Path(member).name
            target.write_bytes(data)
            print(f"{member}: {len(data):#x} -> {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
