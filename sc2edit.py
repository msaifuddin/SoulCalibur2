#!/usr/bin/env python3
"""Read and update player stats on a SoulCalibur II Conquest card.

    python sc2edit.py list   Card0.conquestcard
    python sc2edit.py show   Card0.conquestcard FALCATA
    python sc2edit.py set    Card0.conquestcard FALCATA --wins 777
    python sc2edit.py verify Card0.conquestcard

Editing never writes over the input. `set` writes to `--output` (default: the
input with a `.edited` suffix) and first saves a `.bak` copy of the original
unless told not to. Every write is read back and re-verified before the file is
kept: the blob trailers, the page check values and the edited fields all have to
come back exactly as intended.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import sc2account
import sc2card
import sc2cardfs


class EditError(Exception):
    pass


def load(card: Path) -> tuple[bytes, bytes, list[bytes]]:
    raw = card.read_bytes()
    data = sc2cardfs.card_data(card)
    block = sc2cardfs.read_index(data)
    if not block.valid:
        raise EditError(f"{card}: the account index does not decrypt")
    return raw, data, sc2cardfs.index_records(block)


def accounts_of(records: list[bytes]) -> list[sc2account.Account]:
    found = []
    for position, record in enumerate(records):
        if not any(record):
            continue
        account = sc2account.read_account(record, position)
        if account and account.name:
            found.append(account)
    return found


def resolve(accounts: list[sc2account.Account], wanted: str) -> sc2account.Account:
    """Find one account by name, by bracketed name, or by index number."""
    if wanted.isdigit():
        matches = [a for a in accounts if a.index == int(wanted)]
    else:
        # Names can carry the grid's music-note glyph, which is awkward to type,
        # and the game's own entries are bracketed. Accept the bare word too.
        def plain(text: str) -> str:
            return text.strip("<>").replace("♪", "")

        wanted_plain = plain(wanted)
        matches = [a for a in accounts if a.name == wanted or plain(a.name) == wanted_plain]
    if not matches:
        raise EditError(f"no account called {wanted!r}; try `list` to see the names")
    if len(matches) > 1:
        joined = ", ".join(str(a.index) for a in matches)
        raise EditError(f"{wanted!r} matches several records ({joined}); use the index")
    return matches[0]


def command_list(args) -> int:
    _, _, records = load(args.card)
    accounts = accounts_of(records)
    if args.player_only:
        accounts = [a for a in accounts if not a.name.startswith("<")]
    accounts.sort(key=lambda a: -a.wins if args.by_wins else a.index)
    print(f"{len(accounts)} accounts")
    header = "  idx  name           wins   losses"
    print(header + ("   password" if args.passwords else ""))
    for account in accounts:
        line = f"  {account.index:4d}  {account.name:<12} {account.wins:5d}  {account.losses:5d}"
        if args.passwords:
            line += f"   {account.password or '?'}"
        print(line)
    return 0


def command_show(args) -> int:
    _, _, records = load(args.card)
    account = resolve(accounts_of(records), args.player)
    record = records[account.index]
    print(f"name      {account.name}")
    print(f"index     {account.index}")
    print(f"password  {account.password}")
    print(f"wins      {account.wins}")
    print(f"losses    {account.losses}")
    print(f"record    {record.hex()}")
    return 0


def command_set(args) -> int:
    if all(value is None for value in (args.wins, args.losses, args.rename, args.password)):
        raise EditError("nothing to change: pass --wins, --losses, --rename and/or --password")
    raw, data, records = load(args.card)
    account = resolve(accounts_of(records), args.player)

    def mutate(record: bytes) -> bytes:
        return sc2account.apply_account(
            record, name=args.rename, wins=args.wins, losses=args.losses,
            password=args.password,
        )

    if mutate(records[account.index]) == records[account.index]:
        print("the record already holds those values; nothing to do")
        return 0

    data = sc2cardfs.update_index_record(data, account.index, mutate)
    rebuilt = sc2card.rebuild_card(data, raw)

    output = args.output or args.card.with_suffix(args.card.suffix + ".edited")
    check = verify_image(
        rebuilt, account.index, args.rename, args.wins, args.losses, args.password
    )
    if check:
        raise EditError("refusing to write: " + "; ".join(check))

    if args.dry_run:
        print("dry run: the edit verified, nothing was written")
        return 0
    if not args.no_backup and args.card.exists():
        backup = args.card.with_suffix(args.card.suffix + ".bak")
        if not backup.exists():
            shutil.copy2(args.card, backup)
            print(f"backed up the original to {backup}")
    output.write_bytes(rebuilt)
    after = sc2account.read_account(
        sc2cardfs.index_records(sc2cardfs.read_index(sc2cardfs.card_data(output)))[account.index],
        account.index,
    )
    print(f"wrote {output}")
    print(f"  before  {account}")
    print(f"  after   {after}")
    return 0


def verify_image(rebuilt: bytes, index: int, name, wins, losses, password=None) -> list[str]:
    """Re-read a freshly built image and confirm everything still holds."""
    problems = []
    if len(rebuilt) != sc2card.RAW_CARD_SIZE:
        problems.append(f"image is {len(rebuilt)} bytes, expected {sc2card.RAW_CARD_SIZE}")
        return problems
    pages, _ = sc2card.split_pages(rebuilt, True)
    data = b"".join(pages)
    bad_spare = sc2card.check_spare(rebuilt)
    if bad_spare:
        problems.append(f"{len(bad_spare)} pages have a stale check value (first {bad_spare[0]:#x})")
    for page in sc2cardfs.INDEX_PAGES:
        block = sc2cardfs.read_index(data, page)
        if not block.valid:
            problems.append(f"index copy at page {page:#x} fails its trailer check")
            continue
        record = sc2cardfs.index_records(block)[index]
        account = sc2account.read_account(record, index)
        if account is None:
            problems.append(f"record {index} in page {page:#x} no longer decodes")
            continue
        if name is not None and account.name != name:
            problems.append(f"name came back as {account.name!r}, expected {name!r}")
        if wins is not None and account.wins != wins:
            problems.append(f"wins came back as {account.wins}, expected {wins}")
        if losses is not None and account.losses != losses:
            problems.append(f"losses came back as {account.losses}, expected {losses}")
        if password is not None and account.password != password:
            problems.append(f"password came back as {account.password!r}, expected {password!r}")
    return problems


def command_create(args) -> int:
    """Add a new Conquest opponent: cloned from a live persona, then rewritten."""
    import random

    import sc2persona

    if args.random and args.style:
        raise EditError("pass --random or --style, not both")
    if not args.name.startswith("<") and args.password is None:
        raise EditError("a loginable (unbracketed) name needs --password")

    raw = args.card.read_bytes()
    data = sc2cardfs.card_data(args.card)
    rng = random.Random(args.seed)

    if args.style:
        style = sc2persona.load_style(Path(args.style))
    else:
        character = args.character if args.character is not None else rng.choice(
            [c for c in sc2persona.CHARACTERS if (sc2persona.MOVETABLES / f"{c:02x}.json").exists()]
        )
        style = sc2persona.random_style(character, rng)
    if args.character is not None:
        style.character = args.character
    wins = args.wins if args.wins is not None else rng.randint(20, 160)
    losses = args.losses if args.losses is not None else rng.randint(5, 60)
    password = args.password or "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(4))

    data, position, dropped = sc2persona.create_persona(
        data, args.template, args.name, password, style, wins, losses,
    )
    rebuilt = sc2card.rebuild_card(data, raw)
    problems = verify_image(rebuilt, position, args.name, wins, losses, password)
    if problems:
        raise EditError("refusing to write: " + "; ".join(problems))

    output = args.output or args.card.with_suffix(args.card.suffix + ".edited")
    print(f"persona   {args.name}")
    print(f"character {sc2persona.CHARACTERS.get(style.character, style.character)}")
    print(f"password  {password}")
    print(f"record    {position}  wins {wins}  losses {losses}  moves in style {len(style.prefs)}")
    if dropped:
        print(f"note      history-log entry {dropped!r} fell off the end of the index")
    if args.dry_run:
        print("dry run: verified, nothing written")
        return 0
    if not args.no_backup:
        backup = args.card.with_suffix(args.card.suffix + ".bak")
        if not backup.exists():
            shutil.copy2(args.card, backup)
            print(f"backed up the original to {backup}")
    output.write_bytes(rebuilt)
    print(f"wrote {output}")
    return 0


def command_verify(args) -> int:
    raw = args.card.read_bytes()
    data = sc2cardfs.card_data(args.card)
    problems = []
    bad_spare = sc2card.check_spare(raw)
    if bad_spare:
        problems.append(f"{len(bad_spare)} pages with a stale check value")
    for page in sc2cardfs.INDEX_PAGES:
        if not sc2cardfs.read_index(data, page).valid:
            problems.append(f"index copy at page {page:#x} fails its trailer check")
    slots = sc2cardfs.used_slots(data)
    print(f"pages with a valid check value: {sc2card.PAGE_COUNT - len(bad_spare)}/{sc2card.PAGE_COUNT}")
    print(f"account slots in use: {len(slots)}")
    print(f"accounts in the index: {len(accounts_of(sc2cardfs.index_records(sc2cardfs.read_index(data))))}")
    if problems:
        for problem in problems:
            print(f"problem: {problem}")
        return 1
    print("card is consistent")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)

    listing = commands.add_parser("list", help="show every account and its record")
    listing.add_argument("card", type=Path)
    listing.add_argument("--player-only", action="store_true",
                         help="hide the game's own <BRACKETED> entries")
    listing.add_argument("--by-wins", action="store_true")
    listing.add_argument("--passwords", action="store_true", help="include each account's password")
    listing.set_defaults(handler=command_list)

    show = commands.add_parser("show", help="show one account in detail")
    show.add_argument("card", type=Path)
    show.add_argument("player", help="name, or the index number from `list`")
    show.set_defaults(handler=command_show)

    setter = commands.add_parser("set", help="update an account's stats")
    setter.add_argument("card", type=Path)
    setter.add_argument("player", help="name, or the index number from `list`")
    setter.add_argument("--wins", type=int)
    setter.add_argument("--losses", type=int)
    setter.add_argument("--rename", help="new name, at most 10 grid characters")
    setter.add_argument("--password", help="new password, 1 to 4 grid characters")
    setter.add_argument("--output", type=Path, help="where to write (default: <card>.edited)")
    setter.add_argument("--no-backup", action="store_true")
    setter.add_argument("--dry-run", action="store_true")
    setter.set_defaults(handler=command_set)

    create = commands.add_parser("create", help="add a new Conquest opponent (persona)")
    create.add_argument("card", type=Path)
    create.add_argument("name", help="<BRACKETED> for a CPU-only persona, plain to allow login")
    create.add_argument("--template", default="<KENTON♪>", help="live persona to clone the record shape from")
    create.add_argument("--character", type=lambda x: int(x, 0), help="character id, e.g. 0x12 Astaroth")
    create.add_argument("--style", help="JSON file with ratios and (distance,state,move,weight) prefs")
    create.add_argument("--random", action="store_true", help="random style (default when no --style)")
    create.add_argument("--seed", type=int)
    create.add_argument("--password", help="1-4 grid characters; random if omitted")
    create.add_argument("--wins", type=int)
    create.add_argument("--losses", type=int)
    create.add_argument("--output", type=Path)
    create.add_argument("--no-backup", action="store_true")
    create.add_argument("--dry-run", action="store_true")
    create.set_defaults(handler=command_create)

    verify = commands.add_parser("verify", help="check a card's integrity")
    verify.add_argument("card", type=Path)
    verify.set_defaults(handler=command_verify)

    args = parser.parse_args()
    try:
        return args.handler(args)
    except (EditError, sc2account.AccountError, sc2card.CardFormatError, OSError, ValueError) as error:
        print(f"error: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
