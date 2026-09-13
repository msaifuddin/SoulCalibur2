#!/usr/bin/env python3
"""Find SC2 Conquest roster/profile records in an EE RAM image or live PCSX2."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, asdict
import json
from pathlib import Path
import re

from pine import Pine


NAME_RE = re.compile(rb"<[\x20-\x7e\xb5]{1,10}>")
ORDINAL_RE = re.compile(rb"\d{1,3}(?:st|nd|rd|th)\x00")


@dataclass
class RosterRecord:
    address: int
    name: str
    rank_code: int
    class_code: int
    group_code: int
    flags: int
    character: int
    wins: int
    losses: int


@dataclass
class DisplayRecord:
    address: int
    name: str
    placing: str
    title: str
    wins: int
    losses: int


def clean_name(value: bytes) -> str:
    value = value.replace(b"\xb5", b"")
    return value.strip(b"<>").decode("ascii", "replace")


def scan_roster(data: bytes) -> list[RosterRecord]:
    records = []
    for match in NAME_RE.finditer(data):
        at = match.start()
        if at < 8 or at + 16 > len(data):
            continue
        before = data[at - 8 : at]
        field = data[at : at + 12]
        if any(field[len(match.group()) :]) or any(before[4:]):
            continue
        character, wins, losses, end_flag = data[at + 12 : at + 16]
        if character > 0x30 or end_flag > 1:
            continue
        records.append(
            RosterRecord(
                address=at - 8,
                name=clean_name(match.group()),
                rank_code=before[0],
                class_code=before[1],
                group_code=before[2],
                flags=before[3],
                character=character,
                wins=wins,
                losses=losses,
            )
        )
    return records


def scan_display(data: bytes) -> list[DisplayRecord]:
    records = []
    for match in NAME_RE.finditer(data):
        at = match.start()
        placing_match = ORDINAL_RE.match(data, at + 16)
        if not placing_match:
            continue
        title_raw = data[at + 52 : at + 84].split(b"\0", 1)[0]
        if not title_raw or not all(32 <= byte < 127 for byte in title_raw):
            continue
        records.append(
            DisplayRecord(
                address=at,
                name=clean_name(match.group()),
                placing=placing_match.group()[:-1].decode("ascii"),
                title=title_raw.decode("ascii"),
                wins=int.from_bytes(data[at + 116 : at + 120], "little"),
                losses=int.from_bytes(data[at + 120 : at + 124], "little"),
            )
        )
    return records


def main() -> int:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--ram", type=Path)
    source.add_argument("--live", action="store_true")
    parser.add_argument("--port", type=int, default=28011)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--all-roster", action="store_true")
    args = parser.parse_args()

    if args.ram:
        data = args.ram.read_bytes()[:0x02000000]
    else:
        client = Pine(port=args.port)
        try:
            data = client.read(0, 0x02000000)
        finally:
            client.close()

    roster = scan_roster(data)
    displays = scan_display(data)
    if not args.all_roster:
        roster = [record for record in roster if not (0x490000 <= record.address < 0x4B0000)]
    output = {
        "roster_records": [asdict(record) for record in roster],
        "display_records": [asdict(record) for record in displays],
    }
    if args.json:
        print(json.dumps(output, indent=2))
    else:
        for record in roster:
            print(
                f"roster {record.address:#08x} {record.name:<10} "
                f"W {record.wins:3} L {record.losses:3} char={record.character:#04x} "
                f"codes={record.rank_code:02x}/{record.class_code:02x}/{record.group_code:02x}/{record.flags:02x}"
            )
        for record in displays:
            print(
                f"display {record.address:#08x} {record.placing:>4} {record.name:<10} "
                f"{record.title:<20} W {record.wins:3} L {record.losses:3}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
