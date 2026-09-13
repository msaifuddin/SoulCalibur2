#!/usr/bin/env python3
"""SoulCalibur II Conquest card stream cipher.

Recovered from the System 246 EE code: the encryptor lives at 0x216cb8 and the
matching decryptor at 0x216e08, with the string tail written by 0x216c60.

An encrypted blob is laid out as

    [ciphertext: n bytes][checksum trailer: 2][encrypted "TSEUQNOC" + NUL: 9]

The keystream is a 16-bit LCG seeded with a checksum of the *plaintext*, so the
trailer is what makes decryption possible: it carries that seed, obfuscated with
the first key halfword.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import struct


TAIL_SIZE = 11
KEY_TEXT = b"TSEUQNOC"


def rotr16(value: int, count: int) -> int:
    value &= 0xFFFF
    return ((value >> count) | (value << (16 - count))) & 0xFFFF


def rotl16(value: int, count: int) -> int:
    return rotr16(value, 16 - count)


def advance(state: int) -> int:
    """The step the EE code performs after emitting each keystream byte."""
    return (rotr16(state, 1) * 5 + 1) & 0xFFFF


@dataclass(frozen=True)
class Key:
    """The three halfwords the game pushes on the stack before each call."""

    trailer_mask: int
    checksum_seed: int
    checksum_factor: int
    text: bytes = KEY_TEXT

    @classmethod
    def parse(cls, value: str) -> "Key":
        parts = value.split(":")
        if len(parts) != 3:
            raise argparse.ArgumentTypeError("key must be mask:seed:factor")
        return cls(*(int(part, 0) for part in parts))


# The key the Conquest account table is stored under, taken from the call at
# 0x1b4b18 that (re)encrypts the table before it is written back to the card.
ACCOUNT_KEY = Key(0x545E, 0x0276, 0x0512)

# The same cipher protects the PS2AC05 game blob on the dongle, under its own
# key and closing string.
GAME_KEY = Key(0xEBD7, 0xA21F, 0x000D, b"VersionA")


def checksum(key: Key, plain: bytes) -> int:
    total = key.checksum_seed
    for byte in plain:
        total = (total + (byte * key.checksum_factor & 0xFFFF)) & 0xFFFF
    return total


def keystream_xor(state: int, data: bytes) -> tuple[bytes, int]:
    out = bytearray(len(data))
    for index, byte in enumerate(data):
        out[index] = byte ^ (state & 0xFF)
        state = advance(state)
    return bytes(out), state


def encrypt(key: Key, plain: bytes) -> bytes:
    seed = checksum(key, plain)
    cipher, _ = keystream_xor(seed, plain)
    trailer = rotl16(key.trailer_mask ^ seed, 3)
    tail, _ = string_tail(trailer, key.text)
    return cipher + struct.pack("<H", trailer) + tail


def string_tail(state: int, text: bytes) -> tuple[bytes, int]:
    """Encrypt the NUL-terminated key string that closes a blob (0x216c60)."""
    out = bytearray()
    for byte in text:
        out.append((state ^ byte) & 0xFF)
        state = (rotl16(state, 11) * 5 + 1) & 0xFFFF
    out.append(0)
    return bytes(out), state


def decrypt(key: Key, blob: bytes) -> bytes:
    if len(blob) <= TAIL_SIZE:
        raise ValueError("blob is smaller than its tail")
    body = blob[: -TAIL_SIZE]
    trailer = struct.unpack_from("<H", blob, len(body))[0]
    seed = key.trailer_mask ^ rotr16(trailer, 3)
    plain, _ = keystream_xor(seed, body)
    return plain


def verify(key: Key, blob: bytes) -> bool:
    """True when the blob's trailer agrees with the recovered plaintext."""
    plain = decrypt(key, blob)
    trailer = struct.unpack_from("<H", blob, len(plain))[0]
    return rotl16(key.trailer_mask ^ checksum(key, plain), 3) == trailer


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--offset", type=lambda x: int(x, 0), default=0)
    parser.add_argument("--size", type=lambda x: int(x, 0),
                        help="bytes to read, tail included (default: to end of file)")
    parser.add_argument("--key", type=Key.parse, default=ACCOUNT_KEY)
    parser.add_argument("--encrypt", action="store_true")
    args = parser.parse_args()

    data = args.source.read_bytes()
    end = len(data) if args.size is None else args.offset + args.size
    chunk = data[args.offset : end]
    result = encrypt(args.key, chunk) if args.encrypt else decrypt(args.key, chunk)
    args.output.write_bytes(result)
    state = "encrypted" if args.encrypt else "decrypted"
    print(f"{state} {len(chunk):#x} bytes to {args.output} ({len(result):#x} bytes)")
    if not args.encrypt:
        print(f"trailer check: {'ok' if verify(args.key, chunk) else 'MISMATCH'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
