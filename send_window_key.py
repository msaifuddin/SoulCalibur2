#!/usr/bin/env python3
"""Enumerate a process's windows and optionally post one virtual key."""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import time


user32 = ctypes.WinDLL("user32", use_last_error=True)
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101


def send_key(key: int) -> None:
    extended = 1 if key in (0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28) else 0
    user32.keybd_event(key, 0, extended, 0)
    time.sleep(0.15)
    user32.keybd_event(key, 0, extended | 2, 0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pid", type=int)
    parser.add_argument("--vk", type=lambda value: int(value, 0))
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--send-input", action="store_true")
    parser.add_argument("--title")
    parser.add_argument(
        "--sequence",
        help="comma-separated virtual-key:delay-after pairs, for example 0x35:0.3,0x31:2",
    )
    args = parser.parse_args()
    windows: list[int] = []

    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def callback(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == args.pid:
            windows.append(int(hwnd))
        return True

    user32.EnumWindows(callback, 0)
    for hwnd in windows:
        length = user32.GetWindowTextLengthW(hwnd)
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, len(title))
        visible = bool(user32.IsWindowVisible(hwnd))
        print(f"hwnd={hwnd:#x} visible={visible} title={title.value!r}")
        selected = args.title is None or args.title in title.value
        if selected and args.show:
            user32.ShowWindow(hwnd, 5)
            user32.SetForegroundWindow(hwnd)
        if selected and args.sequence:
            user32.SetForegroundWindow(hwnd)
            for item in args.sequence.split(","):
                key_text, _, delay_text = item.partition(":")
                key = int(key_text, 0)
                send_key(key)
                time.sleep(float(delay_text or "0.25"))
        if selected and args.vk is not None and args.send_input:
            user32.SetForegroundWindow(hwnd)
            send_key(args.vk)
        elif selected and args.vk is not None:
            user32.PostMessageW(hwnd, WM_KEYDOWN, args.vk, 0)
            user32.PostMessageW(hwnd, WM_KEYUP, args.vk, 0xC0000000)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
