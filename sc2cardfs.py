#!/usr/bin/env python3
"""Decode the encrypted layout of a SoulCalibur II Conquest card.

Everything past the plaintext header pages is encrypted with the stream cipher
in `sc2crypto`, under the key the EE code pushes at 0x1b4b18 and 0x1b3560.

Card layout, in data pages of 512 bytes (spare/ECC already stripped):

    page 0x00   "Memory Card for SoulCaliburII ..." magic
    page 0x10   "BBLK" block header
    page 0x20   "MI" account index, 0x831b-byte blob  (primary copy)
    page 0x70   "MI" account index, 0x831b-byte blob  (second copy)
    page 0xc0+  "PD" account slots, 0x2000 bytes each, 0x1ff8-byte blob

All reads here are read-only; nothing writes back to a card image.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import re
from pathlib import Path

import sc2card
import sc2crypto


PAGE = sc2card.PAGE_DATA_SIZE
BLOCK_HEADER_SIZE = 8
INDEX_PAGES = (0x20, 0x70)
INDEX_BLOB_SIZE = 0x831B
INDEX_HEADER_SIZE = 0x24
INDEX_RECORD_SIZE = 0x4C
SLOT_FIRST_PAGE = 0xC0
SLOT_SIZE = 0x2000
SLOT_BLOB_SIZE = 0x1FF8
SLOT_RECORD_SIZE = 0x14
SLOT_RECORD_START = 0x0C
INDEX_TAG = b"MI"
SLOT_TAG = b"PD"


@dataclass
class Block:
    """One encrypted container on the card."""

    page: int
    tag: bytes
    header: bytes
    plain: bytes
    valid: bool

    @property
    def offset(self) -> int:
        return self.page * PAGE


@dataclass
class SlotRecord:
    """A 20-byte entry inside an account slot: a stamp plus packed strings."""

    offset: int
    stamp: int
    fields: bytes
    marker: int
    strings: list[str]


def card_data(path: Path) -> bytes:
    """The card with its spare/ECC bytes removed."""
    raw, has_spare = sc2card.load_card(path)
    pages, _ = sc2card.split_pages(raw, has_spare)
    return b"".join(pages)


def read_block(data: bytes, page: int, blob_size: int) -> Block:
    start = page * PAGE
    header = data[start : start + BLOCK_HEADER_SIZE]
    body = data[start + BLOCK_HEADER_SIZE : start + BLOCK_HEADER_SIZE + blob_size]
    valid = len(body) == blob_size and sc2crypto.verify(sc2crypto.ACCOUNT_KEY, body)
    plain = sc2crypto.decrypt(sc2crypto.ACCOUNT_KEY, body) if valid else b""
    return Block(page=page, tag=header[:2], header=header, plain=plain, valid=valid)


def read_index(data: bytes, page: int = INDEX_PAGES[0]) -> Block:
    return read_block(data, page, INDEX_BLOB_SIZE)


def index_records(block: Block) -> list[bytes]:
    body = block.plain[INDEX_HEADER_SIZE:]
    return [
        body[at : at + INDEX_RECORD_SIZE]
        for at in range(0, len(body) - INDEX_RECORD_SIZE + 1, INDEX_RECORD_SIZE)
    ]


def slot_count(data: bytes) -> int:
    return (len(data) - SLOT_FIRST_PAGE * PAGE) // SLOT_SIZE


def read_slot(data: bytes, index: int) -> Block:
    return read_block(data, SLOT_FIRST_PAGE + index * (SLOT_SIZE // PAGE), SLOT_BLOB_SIZE)


def is_erased(data: bytes, page: int, size: int) -> bool:
    start = page * PAGE
    return data[start : start + size] == b"\xff" * size


def slot_records(block: Block, limit: int | None = None) -> list[SlotRecord]:
    """Walk the packed 20-byte records that open an account slot.

    A record is a 4-byte stamp, four flag bytes, a marker byte and an 11-byte
    area holding NUL-separated strings (the player name, then a short tag).
    """
    records: list[SlotRecord] = []
    body = block.plain
    at = SLOT_RECORD_START
    while at + SLOT_RECORD_SIZE <= len(body):
        chunk = body[at : at + SLOT_RECORD_SIZE]
        text = chunk[9:]
        if not any(chunk):
            break
        strings = [
            part.decode("ascii", "replace")
            for part in text.split(b"\0")
            if part and all(0x20 <= byte < 0x7F for byte in part)
        ]
        records.append(
            SlotRecord(
                offset=at,
                stamp=int.from_bytes(chunk[:4], "little"),
                fields=chunk[4:8],
                marker=chunk[8],
                strings=strings,
            )
        )
        at += SLOT_RECORD_SIZE
        if limit is not None and len(records) >= limit:
            break
    return records


def used_slots(data: bytes) -> list[int]:
    used = []
    for index in range(slot_count(data)):
        page = SLOT_FIRST_PAGE + index * (SLOT_SIZE // PAGE)
        if is_erased(data, page, SLOT_SIZE):
            continue
        if read_slot(data, index).valid:
            used.append(index)
    return used


def splice_block(data: bytes, block: Block, plain: bytes, blob_size: int) -> bytes:
    """Re-encrypt `plain` and place it back where `block` came from.

    The trailer is recomputed from the new plaintext, so the result passes the
    same integrity check the game applies when it reads the blob.
    """
    if len(plain) != len(block.plain):
        raise ValueError("a block's plaintext must keep its original length")
    blob = sc2crypto.encrypt(sc2crypto.ACCOUNT_KEY, plain)
    if len(blob) != blob_size:
        raise ValueError(f"re-encrypted blob is {len(blob):#x}, expected {blob_size:#x}")
    start = block.offset + BLOCK_HEADER_SIZE
    return data[:start] + blob + data[start + blob_size :]


def update_index_record(data: bytes, position: int, mutate) -> bytes:
    """Apply `mutate` to one record in every copy of the account index.

    The copies are separate generations of the same table, each with its own
    header, so each one is edited in place rather than replaced wholesale: only
    the targeted record changes and every other byte survives untouched.
    """
    for page in INDEX_PAGES:
        block = read_index(data, page)
        if not block.valid:
            raise ValueError(f"index page {page:#x} does not decrypt; refusing to write")
        records = index_records(block)
        if not 0 <= position < len(records):
            raise ValueError(f"record {position} is outside the {len(records)}-entry index")
        records[position] = mutate(records[position])
        plain = block.plain[:INDEX_HEADER_SIZE] + b"".join(records)
        data = splice_block(data, block, plain, INDEX_BLOB_SIZE)
    return data


def command_map(args) -> int:
    data = card_data(args.card)
    print(f"data pages: {len(data) // PAGE:#x}")
    for page in INDEX_PAGES:
        block = read_index(data, page)
        records = index_records(block) if block.valid else []
        state = "ok" if block.valid else "unreadable"
        print(
            f"index page {page:#05x}: tag={block.tag.decode('ascii', 'replace')!r} "
            f"{state} plain={len(block.plain):#x} records={len(records)}"
        )
    slots = used_slots(data)
    print(f"account slots: {slot_count(data)} total, {len(slots)} in use")
    if slots:
        print(f"first={slots[0]} last={slots[-1]}")
    return 0


def command_accounts(args) -> int:
    data = card_data(args.card)
    for index in used_slots(data):
        block = read_slot(data, index)
        records = slot_records(block, limit=args.records)
        head = records[0] if records else None
        name = " / ".join(head.strings) if head else "(no records)"
        print(
            f"slot {index:4d} page {block.page:#06x} stamp={head.stamp if head else 0:#010x} "
            f"records={len(records)} {name}"
        )
        if args.verbose:
            for record in records[1:]:
                print(
                    f"     +{record.offset:#05x} stamp={record.stamp:#010x} "
                    f"marker={record.marker:#04x} {' / '.join(record.strings)}"
                )
    return 0


def command_dump(args) -> int:
    data = card_data(args.card)
    block = read_slot(data, args.slot) if args.slot is not None else read_index(data, args.page)
    if not block.valid:
        print("error: that block does not decrypt (checksum trailer mismatch)")
        return 1
    args.output.write_bytes(block.plain)
    print(f"wrote {len(block.plain):#x} plaintext bytes to {args.output}")
    return 0


def command_strings(args) -> int:
    data = card_data(args.card)
    pattern = re.compile(rb"[\x20-\x7e]{%d,}" % args.min_length)
    for index in used_slots(data):
        block = read_slot(data, index)
        found = {match.group().decode("ascii") for match in pattern.finditer(block.plain)}
        if found:
            print(f"slot {index:4d}: " + ", ".join(sorted(found)[: args.limit]))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    mapper = commands.add_parser("map", help="summarize the encrypted layout")
    mapper.add_argument("card", type=Path)
    mapper.set_defaults(handler=command_map)

    accounts = commands.add_parser("accounts", help="list the decrypted account slots")
    accounts.add_argument("card", type=Path)
    accounts.add_argument("--records", type=int, default=8)
    accounts.add_argument("--verbose", action="store_true")
    accounts.set_defaults(handler=command_accounts)

    dump = commands.add_parser("dump", help="write one decrypted block to a file")
    dump.add_argument("card", type=Path)
    dump.add_argument("output", type=Path)
    dump.add_argument("--slot", type=int)
    dump.add_argument("--page", type=lambda x: int(x, 0), default=INDEX_PAGES[0])
    dump.set_defaults(handler=command_dump)

    strings = commands.add_parser("strings", help="printable text inside each account slot")
    strings.add_argument("card", type=Path)
    strings.add_argument("--min-length", type=int, default=3)
    strings.add_argument("--limit", type=int, default=12)
    strings.set_defaults(handler=command_strings)

    args = parser.parse_args()
    try:
        return args.handler(args)
    except (OSError, sc2card.CardFormatError) as error:
        print(f"error: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
