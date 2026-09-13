#!/usr/bin/env python3
"""Read-only tools for SoulCalibur II System 246 Conquest card images."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import struct
from collections import Counter
from pathlib import Path

import sc2crypto


PAGE_DATA_SIZE = 0x200
PAGE_SPARE_SIZE = 0x10
PAGE_RAW_SIZE = PAGE_DATA_SIZE + PAGE_SPARE_SIZE
PAGE_COUNT = 0x4000
RAW_CARD_SIZE = PAGE_RAW_SIZE * PAGE_COUNT
DATA_CARD_SIZE = PAGE_DATA_SIZE * PAGE_COUNT
MAGIC = b"Memory Card for SoulCaliburII (C)1995 1998 2002 NAMCO LTD."


class CardFormatError(ValueError):
    pass


def _entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = Counter(data)
    size = len(data)
    return -sum((count / size) * math.log2(count / size) for count in counts.values())


def load_card(path: Path) -> tuple[bytes, bool]:
    raw = path.read_bytes()
    if len(raw) == RAW_CARD_SIZE:
        return raw, True
    if len(raw) == DATA_CARD_SIZE:
        return raw, False
    raise CardFormatError(
        f"unexpected image size {len(raw):#x}; expected {RAW_CARD_SIZE:#x} "
        f"(with spare/ECC) or {DATA_CARD_SIZE:#x} (data only)"
    )


def split_pages(raw: bytes, has_spare: bool) -> tuple[list[bytes], list[bytes]]:
    stride = PAGE_RAW_SIZE if has_spare else PAGE_DATA_SIZE
    pages = [raw[i * stride : i * stride + PAGE_DATA_SIZE] for i in range(PAGE_COUNT)]
    spare = (
        [raw[i * stride + PAGE_DATA_SIZE : (i + 1) * stride] for i in range(PAGE_COUNT)]
        if has_spare
        else []
    )
    return pages, spare


def inspect_card(path: Path, as_json: bool) -> int:
    raw, has_spare = load_card(path)
    pages, spare = split_pages(raw, has_spare)
    payload = b"".join(pages)
    printable = re.findall(rb"[ -~]{6,}", payload)
    result = {
        "path": str(path.resolve()),
        "size": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "page_count": len(pages),
        "page_data_size": PAGE_DATA_SIZE,
        "page_spare_size": PAGE_SPARE_SIZE if has_spare else 0,
        "has_expected_magic": pages[0].startswith(MAGIC),
        "magic": pages[0].split(b"\0", 1)[0].decode("ascii", "replace"),
        "payload_entropy": round(_entropy(payload[32 * PAGE_DATA_SIZE :]), 6),
        "non_ff_pages": sum(page != b"\xff" * PAGE_DATA_SIZE for page in pages),
        "unique_spare_values": len(set(spare)) if spare else 0,
        "printable_strings": [s.decode("ascii", "replace") for s in printable[:25]],
    }
    if as_json:
        print(json.dumps(result, indent=2))
    else:
        for key, value in result.items():
            if key != "printable_strings":
                print(f"{key}: {value}")
        print("printable_strings:")
        for value in result["printable_strings"]:
            print(f"  {value}")
    return 0 if result["has_expected_magic"] else 2


def compare_cards(old_path: Path, new_path: Path, as_json: bool) -> int:
    old_raw, old_spare = load_card(old_path)
    new_raw, new_spare = load_card(new_path)
    old_pages, _ = split_pages(old_raw, old_spare)
    new_pages, _ = split_pages(new_raw, new_spare)
    changed = []
    changed_bytes = 0
    for index, (old, new) in enumerate(zip(old_pages, new_pages)):
        count = sum(a != b for a, b in zip(old, new))
        if count:
            changed.append({"page": index, "changed_data_bytes": count})
            changed_bytes += count
    result = {
        "old": str(old_path.resolve()),
        "new": str(new_path.resolve()),
        "changed_pages": len(changed),
        "changed_data_bytes": changed_bytes,
        "first_changed_page": changed[0]["page"] if changed else None,
        "last_changed_page": changed[-1]["page"] if changed else None,
        "pages": changed,
    }
    if as_json:
        print(json.dumps(result, indent=2))
    else:
        for key, value in result.items():
            if key != "pages":
                print(f"{key}: {value}")
        print("most changed pages:")
        for item in sorted(changed, key=lambda x: x["changed_data_bytes"], reverse=True)[:20]:
            print(f"  page {item['page']:5d} ({item['page']:#06x}): {item['changed_data_bytes']} bytes")
    return 0


def _crc_table() -> list[int]:
    table = []
    for index in range(256):
        register = index << 24
        for _ in range(8):
            if register & 0x80000000:
                register = ((register << 1) ^ 0x04C11DB7) & 0xFFFFFFFF
            else:
                register = (register << 1) & 0xFFFFFFFF
        table.append(register)
    return table


CRC_TABLE = _crc_table()


def page_edc(page: bytes) -> bytes:
    """The 4-byte check value a Conquest card stores in each page's spare area.

    CRC-32/MPEG-2 (polynomial 0x04c11db7, initial value 0, no reflection and no
    final xor), stored big-endian. Verified against all 16,329 written pages of
    the reference card.
    """
    register = 0
    for byte in page:
        register = ((register << 8) & 0xFFFFFFFF) ^ CRC_TABLE[((register >> 24) ^ byte) & 0xFF]
    return struct.pack(">I", register)


def build_spare(page: bytes) -> bytes:
    """A full 16-byte spare area: the check value, then erased flash."""
    if page == b"\xff" * PAGE_DATA_SIZE:
        return b"\xff" * PAGE_SPARE_SIZE
    return page_edc(page) + b"\xff" * (PAGE_SPARE_SIZE - 4)


def check_spare(raw: bytes) -> list[int]:
    """Page numbers whose stored spare disagrees with a freshly computed one."""
    pages, spare = split_pages(raw, True)
    return [
        number
        for number, (page, stored) in enumerate(zip(pages, spare))
        if stored != b"\xff" * PAGE_SPARE_SIZE and stored != build_spare(page)
    ]


def rebuild_card(data: bytes, template_raw: bytes) -> bytes:
    """Re-assemble a raw card image from data pages, refreshing each page's spare.

    A page that is unchanged keeps its original spare bytes verbatim, so an
    untouched card round-trips to exactly the same bytes.
    """
    out = bytearray()
    for number in range(PAGE_COUNT):
        page = data[number * PAGE_DATA_SIZE : (number + 1) * PAGE_DATA_SIZE]
        start = number * PAGE_RAW_SIZE
        original = template_raw[start : start + PAGE_DATA_SIZE]
        spare = (
            template_raw[start + PAGE_DATA_SIZE : start + PAGE_RAW_SIZE]
            if page == original
            else build_spare(page)
        )
        out += page + spare
    return bytes(out)


def strip_ecc(source: Path, destination: Path) -> int:
    raw, has_spare = load_card(source)
    pages, _ = split_pages(raw, has_spare)
    destination.write_bytes(b"".join(pages))
    print(f"wrote {destination} ({destination.stat().st_size} bytes)")
    return 0


def decrypt_game_blob(source: bytes) -> bytes:
    """Decrypt a PS2AC05 blob.

    The game blob and the Conquest card use the same stream cipher; only the
    key halfwords and the string that closes the blob differ. See `sc2crypto`.
    """
    if len(source) <= sc2crypto.TAIL_SIZE:
        raise CardFormatError("PS2AC05 blob is too small")
    if not sc2crypto.verify(sc2crypto.GAME_KEY, source):
        raise CardFormatError("PS2AC05 checksum trailer does not match the plaintext")
    body_length = len(source) - sc2crypto.TAIL_SIZE
    trailer = struct.unpack_from("<H", source, body_length)[0]
    expected, _ = sc2crypto.string_tail(trailer, sc2crypto.GAME_KEY.text)
    if source[body_length + 2 :] != expected:
        raise CardFormatError("unsupported PS2AC05 version marker")
    return sc2crypto.decrypt(sc2crypto.GAME_KEY, source)


def decompress_game_blob(source: bytes) -> bytes:
    """Port of the game's small flag-byte/back-reference decompressor."""
    src = 0
    output = bytearray()
    control = source[src]
    src += 1
    while control:
        bits = control
        if bits < 2:
            control = source[src]
            src += 1
            continue
        while bits > 1:
            literal = bits & 1
            bits >>= 1
            if literal:
                output.append(source[src])
                src += 1
            else:
                pair = (source[src] << 8) | source[src + 1]
                src += 2
                distance = pair & 0x7FF or 0x800
                length = (pair >> 11) & 0x1F or 0x20
                if distance > len(output):
                    raise CardFormatError(
                        f"invalid RLE back-reference distance {distance:#x} at input {src:#x}"
                    )
                for _ in range(length):
                    output.append(output[-distance])
        control = source[src]
        src += 1
    return bytes(output)


def unpack_game(source: Path, destination: Path) -> int:
    destination.mkdir(parents=True, exist_ok=True)
    encrypted = source.read_bytes()
    decrypted = decrypt_game_blob(encrypted)
    decompressed = decompress_game_blob(decrypted[4:])
    decrypted_path = destination / "PS2AC05.decrypted"
    decompressed_path = destination / "PS2AC05.decompressed"
    decrypted_path.write_bytes(decrypted)
    decompressed_path.write_bytes(decompressed)
    print(f"decrypted: {decrypted_path} ({len(decrypted)} bytes)")
    print(f"decompressed: {decompressed_path} ({len(decompressed)} bytes)")
    print(f"decompressed sha256: {hashlib.sha256(decompressed).hexdigest()}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    inspect = sub.add_parser("inspect", help="validate and summarize a card image")
    inspect.add_argument("card", type=Path)
    inspect.add_argument("--json", action="store_true")
    inspect.set_defaults(run=lambda args: inspect_card(args.card, args.json))

    compare = sub.add_parser("compare", help="locate changed data pages between card images")
    compare.add_argument("old", type=Path)
    compare.add_argument("new", type=Path)
    compare.add_argument("--json", action="store_true")
    compare.set_defaults(run=lambda args: compare_cards(args.old, args.new, args.json))

    strip = sub.add_parser("strip-ecc", help="write an 8 MiB data-only image")
    strip.add_argument("card", type=Path)
    strip.add_argument("output", type=Path)
    strip.set_defaults(run=lambda args: strip_ecc(args.card, args.output))

    unpack = sub.add_parser("unpack-game", help="decrypt and decompress a PS2AC05 game blob")
    unpack.add_argument("blob", type=Path)
    unpack.add_argument("output_directory", type=Path)
    unpack.set_defaults(run=lambda args: unpack_game(args.blob, args.output_directory))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.run(args)
    except (OSError, CardFormatError) as error:
        print(f"error: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
