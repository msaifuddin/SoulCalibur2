#!/usr/bin/env python3
"""Read-only Windows process-memory scanner used for SC2 runtime research."""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from pathlib import Path


PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
MEM_COMMIT = 0x1000
PAGE_GUARD = 0x100
PAGE_NOACCESS = 0x01


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("PartitionId", wintypes.WORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.VirtualQueryEx.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.POINTER(MEMORY_BASIC_INFORMATION),
    ctypes.c_size_t,
]
kernel32.VirtualQueryEx.restype = ctypes.c_size_t
kernel32.ReadProcessMemory.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
kernel32.ReadProcessMemory.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def read_at(handle, address: int, size: int) -> bytes:
    buffer = ctypes.create_string_buffer(size)
    read = ctypes.c_size_t()
    if not kernel32.ReadProcessMemory(
        handle, ctypes.c_void_p(address), buffer, size, ctypes.byref(read)
    ):
        return b""
    return buffer.raw[: read.value]


def scan(handle, needle: bytes):
    address = 0
    mbi = MEMORY_BASIC_INFORMATION()
    max_address = (1 << 47) - 1
    while address < max_address:
        result = kernel32.VirtualQueryEx(
            handle, ctypes.c_void_p(address), ctypes.byref(mbi), ctypes.sizeof(mbi)
        )
        if not result:
            break
        base = int(mbi.BaseAddress or 0)
        size = int(mbi.RegionSize)
        readable = (
            mbi.State == MEM_COMMIT
            and not (mbi.Protect & PAGE_GUARD)
            and not (mbi.Protect & PAGE_NOACCESS)
        )
        if readable and size:
            chunk_size = 4 * 1024 * 1024
            overlap = max(0, len(needle) - 1)
            previous = b""
            cursor = 0
            while cursor < size:
                block = read_at(handle, base + cursor, min(chunk_size, size - cursor))
                if not block:
                    break
                combined = previous + block
                found = combined.find(needle)
                while found >= 0:
                    absolute = base + cursor - len(previous) + found
                    yield absolute, base, size, mbi.Protect, mbi.Type
                    found = combined.find(needle, found + 1)
                previous = combined[-overlap:] if overlap else b""
                cursor += len(block)
        next_address = base + max(size, 0x1000)
        if next_address <= address:
            break
        address = next_address


def regions(handle):
    address = 0
    mbi = MEMORY_BASIC_INFORMATION()
    max_address = (1 << 47) - 1
    while address < max_address:
        result = kernel32.VirtualQueryEx(
            handle, ctypes.c_void_p(address), ctypes.byref(mbi), ctypes.sizeof(mbi)
        )
        if not result:
            break
        base = int(mbi.BaseAddress or 0)
        size = int(mbi.RegionSize)
        if mbi.State == MEM_COMMIT and not (mbi.Protect & (PAGE_GUARD | PAGE_NOACCESS)):
            yield base, size, mbi.Protect, mbi.Type
        next_address = base + max(size, 0x1000)
        if next_address <= address:
            break
        address = next_address


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pid", type=int)
    parser.add_argument("needle", nargs="?")
    parser.add_argument("--hex", action="store_true", dest="is_hex")
    parser.add_argument("--dump-region", type=Path)
    parser.add_argument("--match", type=int, default=0)
    parser.add_argument("--list-regions", action="store_true")
    parser.add_argument("--minimum-size", type=lambda value: int(value, 0), default=0x100000)
    parser.add_argument("--dump-address", type=lambda value: int(value, 0))
    parser.add_argument("--dump-size", type=lambda value: int(value, 0))
    args = parser.parse_args()
    handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, args.pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if args.list_regions:
            for base, size, protect, kind in regions(handle):
                if size >= args.minimum_size:
                    print(
                        f"region={base:#x}+{size:#x} protect={protect:#x} type={kind:#x}",
                        flush=True,
                    )
            return 0
        if args.dump_address is not None:
            if args.dump_region is None or args.dump_size is None:
                parser.error("--dump-address requires --dump-size and --dump-region")
            data = read_at(handle, args.dump_address, args.dump_size)
            args.dump_region.write_bytes(data)
            print(f"dumped {len(data):#x} bytes to {args.dump_region}", flush=True)
            return 0
        if args.needle is None:
            parser.error("needle is required unless --list-regions is used")
        needle = bytes.fromhex(args.needle) if args.is_hex else args.needle.encode("ascii")
        matches = list(scan(handle, needle))
        for index, (address, base, size, protect, kind) in enumerate(matches):
            print(
                f"[{index}] address={address:#x} region={base:#x}+{size:#x} "
                f"protect={protect:#x} type={kind:#x}",
                flush=True,
            )
        if args.dump_region is not None:
            if not matches:
                raise SystemExit("needle not found")
            _, base, size, _, _ = matches[args.match]
            data = read_at(handle, base, size)
            args.dump_region.write_bytes(data)
            print(f"dumped {len(data):#x} bytes to {args.dump_region}", flush=True)
    finally:
        kernel32.CloseHandle(handle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
