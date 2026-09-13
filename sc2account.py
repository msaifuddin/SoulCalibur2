#!/usr/bin/env python3
"""Conquest account records: the bit-packed fields inside a card's index table.

The game reads these through a little bit reader (EE 0x1b1410) that pulls bits
LSB-first within each byte and ascending across bytes. Player names use that
reader too: a 4-bit length followed by 6-bit character codes indexed into the
game's own 49-entry table (EE 0x49b9a0), which is why a name never appears as
plain ASCII on the card.

Field offsets were fixed against the game's own "Fight the enemy" screen, which
prints each account's name with its win and loss totals: all seven names visible
there matched these positions exactly.
"""

from __future__ import annotations

from dataclasses import dataclass

# EE 0x49b9a0. Index 45 is the music-note glyph the name grid offers; the last
# two entries are unused padding.
CHARSET = "7H!<N4J'DY06SPQO2M 3.XBTZ:U-5R9&CK>FW?GL8EI1A♪V"
NAME_BIT = 42 * 8  # where the packed name starts inside an index record
MAX_NAME = 10

# (start bit, width) inside the 0x4c-byte index record.
WINS = (487, 11)
LOSSES = (512, 9)

# The password follows the fixed 64-bit name field: a 4-bit length, then four
# 6-bit glyph codes (unused trailing codes are left as 0). Confirmed against a
# known account/password pair on the reference card.
PASSWORD_LENGTH = (400, 4)
PASSWORD_BIT = 404
MAX_PASSWORD = 4


class AccountError(ValueError):
    pass


def read_bits(data: bytes, bit: int, width: int) -> int:
    value = produced = 0
    while width:
        byte = data[bit // 8]
        offset = bit % 8
        take = min(8 - offset, width)
        value |= ((byte >> offset) & ((1 << take) - 1)) << produced
        produced += take
        width -= take
        bit += take
    return value


def write_bits(data: bytearray, bit: int, width: int, value: int) -> None:
    if value < 0 or value >= (1 << width):
        raise AccountError(f"{value} does not fit in {width} bits")
    while width:
        offset = bit % 8
        take = min(8 - offset, width)
        mask = ((1 << take) - 1) << offset
        data[bit // 8] = (data[bit // 8] & ~mask & 0xFF) | ((value & ((1 << take) - 1)) << offset)
        value >>= take
        produced = take
        width -= produced
        bit += produced


def decode_name(record: bytes, bit: int = NAME_BIT) -> str | None:
    length = read_bits(record, bit, 4)
    bit += 4
    letters = []
    for _ in range(length):
        code = read_bits(record, bit, 6)
        bit += 6
        if code >= len(CHARSET):
            return None
        letters.append(CHARSET[code])
    return "".join(letters)


def encode_name(record: bytearray, name: str, bit: int = NAME_BIT) -> None:
    """Rewrite the packed name in place, clearing the rest of the name field."""
    if len(name) > MAX_NAME:
        raise AccountError(f"names are at most {MAX_NAME} characters")
    codes = []
    for letter in name:
        if letter not in CHARSET:
            raise AccountError(f"{letter!r} is not on the Conquest name grid")
        codes.append(CHARSET.index(letter))
    field_bits = 4 + MAX_NAME * 6
    for offset in range(field_bits):
        write_bits(record, bit + offset, 1, 0)
    write_bits(record, bit, 4, len(codes))
    at = bit + 4
    for code in codes:
        write_bits(record, at, 6, code)
        at += 6


def decode_password(record: bytes) -> str | None:
    length = read_bits(record, *PASSWORD_LENGTH)
    if length > MAX_PASSWORD:
        return None
    letters = []
    for position in range(length):
        code = read_bits(record, PASSWORD_BIT + position * 6, 6)
        if code >= len(CHARSET):
            return None
        letters.append(CHARSET[code])
    return "".join(letters)


def encode_password(record: bytearray, password: str) -> None:
    if not 1 <= len(password) <= MAX_PASSWORD:
        raise AccountError(f"passwords are 1 to {MAX_PASSWORD} characters")
    codes = []
    for letter in password:
        if letter not in CHARSET:
            raise AccountError(f"{letter!r} is not on the Conquest password grid")
        codes.append(CHARSET.index(letter))
    write_bits(record, *PASSWORD_LENGTH, len(codes))
    for position in range(MAX_PASSWORD):
        code = codes[position] if position < len(codes) else 0
        write_bits(record, PASSWORD_BIT + position * 6, 6, code)


@dataclass
class Account:
    """One player as the card's index table stores them."""

    index: int
    name: str
    wins: int
    losses: int
    password: str | None = None

    def __str__(self) -> str:
        return f"{self.index:4d}  {self.name:<12} {self.wins:5d}W {self.losses:5d}L"


def read_account(record: bytes, index: int) -> Account | None:
    name = decode_name(record)
    if name is None:
        return None
    return Account(
        index=index,
        name=name,
        wins=read_bits(record, *WINS),
        losses=read_bits(record, *LOSSES),
        password=decode_password(record),
    )


def apply_account(record: bytes, name: str | None = None,
                  wins: int | None = None, losses: int | None = None,
                  password: str | None = None) -> bytes:
    """Return a copy of `record` with the named fields replaced."""
    updated = bytearray(record)
    if name is not None:
        encode_name(updated, name)
    if password is not None:
        encode_password(updated, password)
    if wins is not None:
        if not 0 <= wins < (1 << WINS[1]):
            raise AccountError(f"wins must be 0..{(1 << WINS[1]) - 1}")
        write_bits(updated, *WINS, wins)
    if losses is not None:
        if not 0 <= losses < (1 << LOSSES[1]):
            raise AccountError(f"losses must be 0..{(1 << LOSSES[1]) - 1}")
        write_bits(updated, *LOSSES, losses)
    return bytes(updated)
