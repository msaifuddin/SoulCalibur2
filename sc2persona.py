#!/usr/bin/env python3
"""Create Conquest CPU opponents ("personas") on a card.

A persona is an index record plus a pair of account slots holding its
play-style profile. The game finds the slots through two 10-bit fields in the
record (`word[0x44] >> 17` and `word[0x48] & 0x3ff`, both stored +12, with
bit 0x20000000 of `word[0x34]` selecting the current one) — decoded from the
load task at EE 0x1b3e00 and verified on all 41 personas of the sample card.

Creating one clones an existing persona wholesale (so every field the game
validates stays valid), then rewrites: name, password, character, wins,
losses, and the whole play-style profile in both slots.

Styles are authored as (situation, move-name, weight) lists in numpad
notation; move names resolve against the character's command table captured
from the running game (`movetables/<char>.json`). To capture another
character's table: dump EE RAM while that character is in a fight and run
`python sc2persona.py capture <dump.bin>`.
"""

from __future__ import annotations

import json
import random
import struct
from dataclasses import dataclass, field
from pathlib import Path

import sc2account as A
import sc2cardfs as F
import sc2crypto

ROOT = Path(__file__).resolve().parent
MOVETABLES = ROOT / "movetables"   # <character id>.json: {move name: command-table index}

# 5-bit character id at bit 432; matched the ELF roster's character byte on
# all 37 personas that have one.
CHARACTER_BIT, CHARACTER_WIDTH = 432, 5
SLOT_BASE = 12                # slot numbers in the record are stored +12
OPPONENTS_AT = 0x0C           # eight recent-opponent records in the slot
OPPONENT_SIZE = 0x14
PROFILE_AT = 0xB0
PROFILE_SIZE = 0x1F2C
HIST_IDS = 0x2C
HIST_COUNTS = 0xFAC
HIST_ROWS = 8
HIST_COLS = 0x1F0

DISTANCE = {"point-blank": 0, "close": 1, "mid-close": 2, "mid": 3, "far": 4, "very-far": 5}
STATE = {"neutral": 0, "walking": 1, "downed": 2, "air": 5}

# Character ids as the game numbers them (command-table prefix cmd_XX).
CHARACTERS = {
    0x01: "Mitsurugi", 0x03: "Taki", 0x04: "Sophitia", 0x05: "Siegfried", 0x0b: "Ivy",
    0x0c: "Kilik", 0x0d: "Xianghua", 0x0f: "Maxi", 0x11: "Voldo", 0x12: "Astaroth",
    0x13: "Nightmare", 0x14: "Cervantes", 0x15: "Raphael", 0x16: "Talim", 0x17: "Yunsung",
    0x1a: "Cassandra",
}


@dataclass
class Style:
    """A play style: preferences per situation, in numpad notation."""

    character: int
    ratios: tuple[float, float, float, float]
    prefs: list[tuple[str, str, str, int]] = field(default_factory=list)
    # (distance, state, move name, weight 1..255)


def game_time() -> int:
    """Seconds since 2000-01-01, the epoch the game's date decoder uses."""
    import datetime
    epoch = datetime.datetime(2000, 1, 1)
    return int((datetime.datetime.now() - epoch).total_seconds())


def slot_links(record: bytes) -> tuple[int, int, bool]:
    w34, w44, w48 = (struct.unpack_from("<I", record, o)[0] for o in (0x34, 0x44, 0x48))
    return ((w44 >> 17) & 0x3FF) - SLOT_BASE, (w48 & 0x3FF) - SLOT_BASE, bool(w34 & 0x20000000)


def set_slot_links(record: bytearray, slot_a: int, slot_b: int, current_b: bool) -> None:
    w34, w44, w48 = (struct.unpack_from("<I", record, o)[0] for o in (0x34, 0x44, 0x48))
    w44 = (w44 & ~(0x3FF << 17)) | (((slot_a + SLOT_BASE) & 0x3FF) << 17)
    w48 = (w48 & ~0x3FF) | ((slot_b + SLOT_BASE) & 0x3FF)
    w34 = (w34 | 0x20000000) if current_b else (w34 & ~0x20000000)
    for o, w in ((0x34, w34), (0x44, w44), (0x48, w48)):
        struct.pack_into("<I", record, o, w & 0xFFFFFFFF)


def character_of(record: bytes) -> int:
    return A.read_bits(record, CHARACTER_BIT, CHARACTER_WIDTH)


def set_character(record: bytearray, character: int) -> None:
    A.write_bits(record, CHARACTER_BIT, CHARACTER_WIDTH, character)


def load_movetable(character: int) -> dict[str, int]:
    path = MOVETABLES / f"{character:02x}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"no move table for character {character:#x} ({CHARACTERS.get(character, '?')}); "
            f"capture it with `sc2persona.py capture` while that character is in a fight"
        )
    return json.loads(path.read_text())


def build_profile(style: Style, template: bytes) -> bytes:
    """A profile buffer: the template's aggregates, our ratios and histogram."""
    table = load_movetable(style.character)
    out = bytearray(template[:PROFILE_SIZE])
    struct.pack_into("<4f", out, 0, *style.ratios)
    ids = bytearray(HIST_ROWS * HIST_COLS)
    counts = bytearray(HIST_ROWS * HIST_COLS)
    rows_used: dict[int, int] = {}
    for distance, state, move, weight in style.prefs:
        if move not in table:
            raise KeyError(f"{move!r} is not in {CHARACTERS.get(style.character)}'s command table")
        col = table[move]
        sid = DISTANCE[distance] | (STATE[state] << 3)
        row = rows_used.get(col, 0)
        if row >= HIST_ROWS:
            raise ValueError(f"more than {HIST_ROWS} situations for {move}")
        ids[row * HIST_COLS + col] = sid
        counts[row * HIST_COLS + col] = max(1, min(255, weight))
        rows_used[col] = row + 1
    out[HIST_IDS:HIST_IDS + len(ids)] = ids
    out[HIST_COUNTS:HIST_COUNTS + len(counts)] = counts
    return bytes(out)


def write_slot_plain(data: bytes, slot: int, plain: bytes) -> bytes:
    block = F.read_slot(data, slot)
    blob = sc2crypto.encrypt(sc2crypto.ACCOUNT_KEY, plain)
    start = block.offset + F.BLOCK_HEADER_SIZE
    return data[:start] + blob + data[start + F.SLOT_BLOB_SIZE:]


def free_slots(data: bytes, count: int) -> list[int]:
    used = set(F.used_slots(data))
    # slot 161 has a header but fails its trailer; treat anything past the last
    # valid slot as free, avoiding the immediately following one to be safe
    free = [s for s in range(max(used) + 2, F.slot_count(data)) if s not in used]
    if len(free) < count:
        raise RuntimeError("not enough free slots on the card")
    return free[:count]


ACTIVE_COUNT_AT = 0x0C  # halfword in the index header: number of live accounts


def active_count(index_plain: bytes) -> int:
    """How many index records the game treats as live accounts.

    The name search at EE 0x1af630 walks exactly this many records; everything
    after them is a history log of one-session players (no slot, not
    loginable, never drawn as an opponent). On the sample card it is 41: the
    40 shipped personas plus the owner's account.
    """
    return struct.unpack_from("<H", index_plain, ACTIVE_COUNT_AT)[0]


def create_persona(data: bytes, template_name: str, name: str, password: str,
                   style: Style, wins: int, losses: int) -> tuple[bytes, int, str | None]:
    """Return (new card data, index position, dropped log entry) with the persona added.

    The persona is inserted at the end of the live region and the live count
    is incremented, so the game's name search and enemy draw both see it. The
    history log after the live region shifts down one; its last entry falls
    off the 441-record table and is reported (an empty record if there was
    room, otherwise a one-session player's log line — never a live account).
    """
    index = F.read_index(data)
    records = F.index_records(index)
    count = active_count(index.plain)
    names = {A.decode_name(r): i for i, r in enumerate(records) if any(r)}
    if name in names:
        raise ValueError(f"an account called {name!r} already exists")
    if template_name not in names or names[template_name] >= count:
        raise ValueError(f"template {template_name!r} is not a live account")
    template = records[names[template_name]]
    tmpl_a, tmpl_b, _ = slot_links(template)
    template_plain = F.read_slot(data, tmpl_a).plain

    position = count
    dropped_record = records[-1]
    dropped = A.decode_name(dropped_record) if any(dropped_record) else None
    slot_a, slot_b = free_slots(data, 2)

    record = bytearray(template)
    A.encode_name(record, name)
    A.encode_password(record, password)
    set_character(record, style.character)
    A.write_bits(record, *A.WINS, wins)
    A.write_bits(record, *A.LOSSES, losses)
    set_slot_links(record, slot_a, slot_b, current_b=False)
    # The record opens with two timestamps (seconds since 2000-01-01, decoded
    # by EE 0x1b1808): registered and last played. Stamp both as now.
    now = game_time()
    struct.pack_into("<II", record, 0, now, now)

    profile = build_profile(style, template_plain[PROFILE_AT:])
    plain = bytearray(template_plain)
    A.encode_name(plain, name, bit=16)
    # A new account has no history: clear the eight recent-opponent records so
    # the game does not narrate the template's past on login.
    plain[OPPONENTS_AT:OPPONENTS_AT + 8 * OPPONENT_SIZE] = bytes(8 * OPPONENT_SIZE)
    plain[PROFILE_AT:PROFILE_AT + PROFILE_SIZE] = profile

    data = insert_index_record(data, position, bytes(record))
    data = write_slot_plain(data, slot_a, bytes(plain))
    data = write_slot_plain(data, slot_b, bytes(plain))
    return data, position, dropped


def insert_index_record(data: bytes, position: int, record: bytes) -> bytes:
    """Insert `record` at `position` in every index copy, bump the live count.

    Records after `position` shift down one; the last record of the table is
    discarded. Both index generations get the same insertion.
    """
    for page in F.INDEX_PAGES:
        block = F.read_index(data, page)
        if not block.valid:
            raise ValueError(f"index page {page:#x} does not decrypt; refusing to write")
        header = bytearray(block.plain[:F.INDEX_HEADER_SIZE])
        struct.pack_into("<H", header, ACTIVE_COUNT_AT, active_count(header) + 1)
        records = F.index_records(block)
        records = records[:position] + [record] + records[position:-1]
        data = F.splice_block(data, block, bytes(header) + b"".join(records), F.INDEX_BLOB_SIZE)
    return data


def load_style(path: Path) -> Style:
    """A style from JSON: {"character": 18, "ratios": [..4..], "prefs": [[dist, state, move, w], ...]}."""
    spec = json.loads(path.read_text())
    return Style(
        character=int(spec["character"]),
        ratios=tuple(float(v) for v in spec["ratios"]),
        prefs=[(d, s, m, int(w)) for d, s, m, w in spec["prefs"]],
    )


def random_style(character: int, rng: random.Random) -> Style:
    """A plausible random style from the character's whole command table."""
    table = load_movetable(character)
    moves = [m for m in table if not m.endswith("_RAND") and "INF_" not in m]
    prefs = []
    for distance in ("point-blank", "close", "mid-close", "mid"):
        for move in rng.sample(moves, min(6, len(moves))):
            prefs.append((distance, "neutral", move, rng.randint(20, 200)))
    for move in rng.sample(moves, 4):
        prefs.append(("close", "downed", move, rng.randint(40, 200)))
    for move in rng.sample(moves, 3):
        prefs.append(("point-blank", "air", move, rng.randint(40, 160)))
    ratios = tuple(round(rng.uniform(0.05, 0.6), 3) for _ in range(4))
    return Style(character=character, ratios=ratios, prefs=prefs)


def capture_movetables(dump: Path) -> list[int]:
    """Extract every character command table present in a flat EE RAM dump."""
    import re

    ram = dump.read_bytes()
    MOVETABLES.mkdir(parents=True, exist_ok=True)
    captured = []
    for match in re.finditer(rb"cmd_([0-9a-f]{2})_INF_8ABG\x00", ram):
        base = match.start() - 4
        if base % 4:
            continue
        character = int(match.group(1), 16)
        names: dict[str, int] = {}
        for index in range(HIST_COLS):
            entry = base + index * 0x30
            raw = ram[entry + 4 : entry + 0x2A].split(b"\0", 1)[0]
            if not raw.startswith(b"cmd_"):
                break
            names[raw.decode("latin-1")[7:]] = index
        if len(names) > 50:
            (MOVETABLES / f"{character:02x}.json").write_text(json.dumps(names, indent=0))
            captured.append(character)
    return captured


if __name__ == "__main__":
    import sys

    if len(sys.argv) == 3 and sys.argv[1] == "capture":
        for character in capture_movetables(Path(sys.argv[2])):
            print(f"captured {CHARACTERS.get(character, '?')} ({character:#04x})")
    else:
        print(__doc__)
