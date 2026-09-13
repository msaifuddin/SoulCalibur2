#!/usr/bin/env python3
"""Drive the isolated lab emulator: launch, send inputs, screenshot, dump EE RAM.

Every command works on the copied lab tree under research-data/lab-emulator.
The user's live card is never touched.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import socket
import subprocess
import time
from pathlib import Path

from pine import Pine, PineError


ROOT = Path(__file__).resolve().parent
LAB = ROOT / "research-data" / "lab-emulator"
EXE = LAB / "pcsx2-qt.exe"
GAME = LAB / "Soul Calibur 2 Lab.acgame"
SNAPS = LAB / "snaps"
PID_FILE = ROOT / "research-data" / "state" / "lab.pid"

user32 = ctypes.WinDLL("user32", use_last_error=True)

# Named keys the JVS bindings and PCSX2 hotkeys use, plus the digits/letters.
KEYS = {
    "up": 0x57, "down": 0x53, "left": 0x41, "right": 0x44,
    "horizontal": 0x5A, "vertical": 0x58, "kick": 0x43, "guard": 0x56,
    "coin": 0x35, "start": 0x31,
    "savestate": 0x70, "screenshot": 0x77, "escape": 0x1B, "pause": 0x20,
}
for _code in range(0x30, 0x3A):
    KEYS[chr(_code)] = _code
for _code in range(0x41, 0x5B):
    KEYS[chr(_code).lower()] = _code
for _index in range(1, 13):
    KEYS[f"f{_index}"] = 0x6F + _index


def resolve_key(name: str) -> int:
    key = name.strip().lower()
    if key in KEYS:
        return KEYS[key]
    if key.startswith("0x"):
        return int(key, 16)
    raise SystemExit(f"unknown key {name!r}; known: {', '.join(sorted(KEYS))}")


def windows_of(pid: int) -> list[int]:
    found: list[int] = []
    prototype = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @prototype
    def callback(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            found.append(int(hwnd))
        return True

    user32.EnumWindows(callback, 0)
    return found


def focus(pid: int) -> int:
    for hwnd in windows_of(pid):
        user32.ShowWindow(hwnd, 5)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.2)
        if user32.GetForegroundWindow() == hwnd:
            return hwnd
    raise SystemExit(f"could not focus a window of pid {pid}")


def tap(key: int, hold: float = 0.12) -> None:
    extended = 2 if key in (0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28) else 0
    user32.keybd_event(key, 0, extended, 0)
    time.sleep(hold)
    user32.keybd_event(key, 0, extended | 2, 0)


def read_pid() -> int:
    if not PID_FILE.exists():
        raise SystemExit("no lab.pid; run `lab.py launch` first")
    pid = int(PID_FILE.read_text().strip())
    if not windows_of(pid) and not pine_alive():
        raise SystemExit(f"lab pid {pid} is not running")
    return pid


def pine_alive(port: int = 28011) -> bool:
    try:
        client = Pine(port=port)
    except (OSError, PineError):
        return False
    try:
        return bool(client.text(8))
    except (OSError, PineError):
        return False
    finally:
        client.close()


def wait_for_pine(port: int, timeout: float) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            client = Pine(port=port)
        except (OSError, PineError):
            time.sleep(1.0)
            continue
        try:
            return client.text(8)
        except (OSError, PineError):
            time.sleep(1.0)
        finally:
            client.close()
    raise SystemExit(f"PINE did not answer on port {port} within {timeout:.0f}s")


def command_launch(args) -> int:
    if PID_FILE.exists() and pine_alive(args.port):
        print(f"lab already running (pid {PID_FILE.read_text().strip()})")
        return 0
    process = subprocess.Popen([str(EXE), str(GAME)], cwd=str(LAB))
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(process.pid))
    version = wait_for_pine(args.port, args.timeout)
    print(f"pid={process.pid} pine={version}")
    client = Pine(port=args.port)
    try:
        print(f"title={client.text(11)} game_id={client.text(12)}")
    except PineError:
        # PINE answers the version query before the VM has a running title.
        print("title not available yet; the VM is still booting")
    finally:
        client.close()
    return 0


def command_keys(args) -> int:
    pid = read_pid()
    focus(pid)
    for item in args.sequence.split(","):
        name, _, delay = item.partition(":")
        tap(resolve_key(name))
        wait = float(delay) if delay else 0.35
        print(f"sent {name.strip()} then waited {wait:.2f}s")
        time.sleep(wait)
    return 0


def command_shot(args) -> int:
    pid = read_pid()
    SNAPS.mkdir(exist_ok=True)
    before = {path.name for path in SNAPS.glob("*.png")}
    focus(pid)
    tap(KEYS["screenshot"])
    print(capture(args.output, before))
    return 0


# The Conquest name/password grid, as drawn on the NAME ENTRY screen.
GRID = (
    "ABCDEFGHIJK",
    "LMNOPQRSTUV",
    "WXYZ&!?.-:'",
    "1234567890♪",
)


def grid_position(letter: str) -> tuple[int, int]:
    for row, line in enumerate(GRID):
        column = line.find(letter)
        if column >= 0:
            return row, column
    raise SystemExit(f"{letter!r} is not on the Conquest input grid")


def grid_path(text: str, start: str = "A") -> list[str]:
    """Key names that walk the grid cursor through `text` and press Input."""
    row, column = grid_position(start)
    keys: list[str] = []
    for letter in text.upper():
        target_row, target_column = grid_position(letter)
        keys += ["down"] * (target_row - row) + ["up"] * (row - target_row)
        keys += ["right"] * (target_column - column) + ["left"] * (column - target_column)
        keys.append("horizontal")
        row, column = target_row, target_column
    return keys


def capture(output: Path | None, before: set[str], timeout: float = 6.0) -> Path:
    """Wait for a new screenshot, nudging the VM if it turns out to be paused.

    PCSX2 only writes a screenshot when it renders a frame, so a paused VM
    silently produces nothing. Rather than guess the pause state, toggle it and
    ask again.
    """
    for attempt in range(3):
        deadline = time.time() + timeout
        while time.time() < deadline:
            new = [path for path in SNAPS.glob("*.png") if path.name not in before]
            if new:
                shot = max(new, key=lambda path: path.stat().st_mtime)
                settle(shot)
                if output:
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_bytes(shot.read_bytes())
                    return output
                return shot
            time.sleep(0.2)
        if attempt < 2:
            tap(KEYS["pause"])
            time.sleep(0.5)
            tap(KEYS["screenshot"])
    raise SystemExit("no screenshot appeared in snaps/")


def command_step(args) -> int:
    """Run the VM only for the length of one input burst, then pause again.

    The Conquest menus are on short timers, so single-stepping this way keeps a
    screen alive while its RAM is examined.
    """
    pid = read_pid()
    SNAPS.mkdir(exist_ok=True)
    before = {path.name for path in SNAPS.glob("*.png")}
    focus(pid)
    if not args.running:
        tap(KEYS["pause"])
        time.sleep(0.25)
    for item in filter(None, args.sequence.split(",")):
        name, _, delay = item.partition(":")
        tap(resolve_key(name))
        time.sleep(float(delay) if delay else 0.3)
    tap(KEYS["screenshot"])
    time.sleep(0.35)
    tap(KEYS["pause"])
    print(capture(args.output, before))
    return 0


def command_type(args) -> int:
    keys = grid_path(args.text, args.start)
    if args.enter:
        keys.append("start")
    args.sequence = ",".join(f"{key}:{args.delay}" for key in keys)
    return command_step(args)


def settle(path: Path, timeout: float = 5.0) -> None:
    """Wait until PCSX2 has finished writing a screenshot before reading it."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with path.open("rb"):
                return
        except PermissionError:
            time.sleep(0.2)
    raise SystemExit(f"{path} stayed locked")


def command_dump(args) -> int:
    read_pid()
    client = Pine(port=args.port)
    try:
        started = time.time()
        data = client.read(args.address, args.size)
    finally:
        client.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(data)
    print(f"wrote {len(data):#x} bytes to {args.output} in {time.time() - started:.1f}s")
    return 0


def command_stop(args) -> int:
    if not PID_FILE.exists():
        print("no lab.pid")
        return 0
    pid = int(PID_FILE.read_text().strip())
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False)
    PID_FILE.unlink()
    print(f"stopped {pid}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=28011)
    commands = parser.add_subparsers(dest="command", required=True)

    launch = commands.add_parser("launch")
    launch.add_argument("--timeout", type=float, default=120.0)
    launch.set_defaults(handler=command_launch)

    keys = commands.add_parser("keys")
    keys.add_argument("sequence", help="comma separated name:delay, e.g. coin:0.4,start:2")
    keys.set_defaults(handler=command_keys)

    step = commands.add_parser("step")
    step.add_argument("sequence", help="keys to send while the VM runs, e.g. right:0.3,horizontal")
    step.add_argument("--output", type=Path)
    step.add_argument("--running", action="store_true", help="the VM is not paused right now")
    step.set_defaults(handler=command_step)

    typed = commands.add_parser("type")
    typed.add_argument("text", help="name or password to enter on the Conquest grid")
    typed.add_argument("--start", default="A", help="grid letter the cursor is on now")
    typed.add_argument("--delay", type=float, default=0.2)
    typed.add_argument("--enter", action="store_true", help="press Start when done")
    typed.add_argument("--output", type=Path)
    typed.add_argument("--running", action="store_true")
    typed.set_defaults(handler=command_type)

    shot = commands.add_parser("shot")
    shot.add_argument("--output", type=Path)
    shot.set_defaults(handler=command_shot)

    dump = commands.add_parser("dump")
    dump.add_argument("output", type=Path)
    dump.add_argument("--address", type=lambda x: int(x, 0), default=0)
    dump.add_argument("--size", type=lambda x: int(x, 0), default=0x02000000)
    dump.set_defaults(handler=command_dump)

    stop = commands.add_parser("stop")
    stop.set_defaults(handler=command_stop)

    args = parser.parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
