#!/usr/bin/env python3
"""Small PCSX2 PINE client for reading and writing EE memory."""

from __future__ import annotations

import argparse
import socket
import struct
from pathlib import Path


READ8, READ16, READ32, READ64 = range(4)
WRITE8, WRITE16, WRITE32, WRITE64 = range(4, 8)
VERSION, SAVE_STATE, LOAD_STATE, TITLE, GAME_ID = 8, 9, 10, 11, 12


class PineError(RuntimeError):
    pass


class Pine:
    def __init__(self, host: str = "127.0.0.1", port: int = 28011):
        self.sock = socket.create_connection((host, port), timeout=5)

    def close(self) -> None:
        self.sock.close()

    def request(self, commands: bytes) -> bytes:
        packet = struct.pack("<I", len(commands) + 4) + commands
        self.sock.sendall(packet)
        header = self._receive(4)
        size = struct.unpack("<I", header)[0]
        body = self._receive(size - 4)
        if not body or body[0] != 0:
            raise PineError("PCSX2 rejected the PINE request")
        return body[1:]

    def _receive(self, size: int) -> bytes:
        output = bytearray()
        while len(output) < size:
            chunk = self.sock.recv(size - len(output))
            if not chunk:
                raise PineError("PCSX2 closed the PINE connection")
            output.extend(chunk)
        return bytes(output)

    def read(self, address: int, size: int) -> bytes:
        output = bytearray()
        while size:
            count64 = min(size // 8, 40_000)
            if count64:
                commands = b"".join(
                    struct.pack("<BI", READ64, address + index * 8)
                    for index in range(count64)
                )
                output.extend(self.request(commands))
                used = count64 * 8
            else:
                commands = b"".join(
                    struct.pack("<BI", READ8, address + index) for index in range(size)
                )
                output.extend(self.request(commands))
                used = size
            address += used
            size -= used
        return bytes(output)

    def write(self, address: int, data: bytes) -> None:
        cursor = 0
        while cursor < len(data):
            count64 = min((len(data) - cursor) // 8, 40_000)
            if count64:
                commands = b"".join(
                    struct.pack(
                        "<BIQ", WRITE64, address + cursor + index * 8,
                        int.from_bytes(data[cursor + index * 8 : cursor + index * 8 + 8], "little"),
                    )
                    for index in range(count64)
                )
                used = count64 * 8
            else:
                used = len(data) - cursor
                commands = b"".join(
                    struct.pack("<BIB", WRITE8, address + cursor + index, data[cursor + index])
                    for index in range(used)
                )
            self.request(commands)
            cursor += used

    def text(self, opcode: int) -> str:
        data = self.request(bytes([opcode]))
        if not data:
            return ""
        length = struct.unpack_from("<I", data)[0]
        return data[4 : 4 + length].decode("utf-8", "replace")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=28011)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("info")
    read = commands.add_parser("read")
    read.add_argument("address", type=lambda x: int(x, 0))
    read.add_argument("size", type=lambda x: int(x, 0))
    read.add_argument("--output", type=Path)
    write = commands.add_parser("write")
    write.add_argument("address", type=lambda x: int(x, 0))
    write.add_argument("hex_data")
    write.add_argument("--yes", action="store_true")
    args = parser.parse_args()

    client = Pine(port=args.port)
    try:
        if args.command == "info":
            print(f"version={client.text(VERSION)}")
            print(f"title={client.text(TITLE)}")
            print(f"game_id={client.text(GAME_ID)}")
        elif args.command == "read":
            data = client.read(args.address, args.size)
            if args.output:
                args.output.write_bytes(data)
                print(f"wrote {len(data):#x} bytes to {args.output}")
            else:
                print(data.hex())
        elif args.command == "write":
            if not args.yes:
                raise SystemExit("write requires --yes")
            client.write(args.address, bytes.fromhex(args.hex_data))
            print("write complete")
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
