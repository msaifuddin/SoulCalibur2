# SC2 Conquest card reverse-engineering progress

Updated 2026-09-13. The user's live card was never modified. All work used copies
under `research-data/`, which is ignored by Git.

## Confirmed inputs

- Live source card: `C:\games\pcsx2x6-v0.2.20-windows-x64-Qt\memcards\Card0.conquestcard`
- Raw size: `0x840000` (16,384 physical pages of 512 data + 16 spare bytes)
- Magic: `Memory Card for SoulCaliburII (C)1995 1998 2002 NAMCO LTD.`
- Game: `NM00007`, SC23 Ver.A10, Conquest ID shown by the game: `7678` (Ver. D)

## The card cipher (solved)

The encryptor is at EE address `0x216cb8`, the decryptor at `0x216e08`, and the
routine that closes a blob with an encrypted string at `0x216c60`. A blob is

    [ciphertext: n bytes][checksum trailer: 2][encrypted key string + NUL: 9]

A key is three 16-bit halfwords plus that closing string. Decryption:

1. `seed = trailer_mask XOR rotr16(trailer, 3)`
2. for each byte: `out = in XOR (state & 0xff)`, then
   `state = (rotr16(state, 1) * 5 + 1) & 0xffff`, starting from `state = seed`
3. the trailer is verifiable: `seed == (checksum_seed + sum(plain_byte *
   checksum_factor)) & 0xffff`, which is what `sc2crypto.verify` checks

Keys found so far:

| Blob | mask | seed | factor | string |
| --- | --- | --- | --- | --- |
| Conquest card | `0x545e` | `0x0276` | `0x0512` | `TSEUQNOC` |
| `PS2AC05` game blob | `0xebd7` | `0xa21f` | `0x000d` | `VersionA` |

The card key is pushed on the stack at the call sites `0x1b3560` (load) and
`0x1b4b18` (store). The same cipher with per-call keys is used elsewhere: a
`0x1ff8` blob at `0x1b05ec`, and blobs keyed by static structures at `0x76ce20`
(size `0x1000`) and `0x76cf00` (size `0x69`).

`sc2crypto.decrypt` reproduces the game's own in-RAM plaintext byte for byte
(0x8310 bytes compared against EE `0x3b8448`), and re-derives `PS2AC05.decrypted`
exactly, so `sc2card.decrypt_game_blob` now delegates to it.

## The page check value (solved)

Each 512-byte data page carries a 4-byte check value in the first bytes of its
16-byte spare area, big-endian, with the remaining 12 bytes left erased (`0xff`).
It is **CRC-32/MPEG-2**: polynomial `0x04c11db7`, initial value 0, no input or
output reflection, no final xor. This is not the standard PS2 Hamming ECC.
Verified against all 16,329 written pages of the reference card, with zero
mismatches; the 55 remaining pages are erased and have an all-`0xff` spare.

`sc2card.page_edc` computes it and `sc2card.rebuild_card` refreshes only the
pages that actually changed, so an unedited card rebuilds to identical bytes.

## The account record (solved)

Index records are bit-packed and read through the game's bit reader at EE
`0x1b1410`, which takes bits LSB-first within a byte and ascending across bytes.
Confirmed field positions inside the `0x4c`-byte record:

| Field | Start bit | Bits used | Notes |
| --- | --- | --- | --- |
| name | 336 (`0x2a.0`) | 4 + 6 per character | length nibble, then 6-bit codes; field is 64 bits |
| password | 400 (`0x32.0`) | 4 + 4 × 6 | length nibble, then four 6-bit codes, unused = 0 |
| wins | 487 (`0x3c.7`) | 11 (max seen 1125) | bits 498-511 unused card-wide |
| losses | 512 (`0x40.0`) | 9 (max seen 265) | bits 521+ unused card-wide |

Names index the game's 49-entry table at EE `0x49b9a0`:
`7H!<N4J'DY06SPQO2M 3.XBTZ:U-5R9&CK>FW?GL8EI1A♪V`. The game's own CPU entries
have bracketed names (`<FALCATA>`); names registered at the cabinet do not, which
is why entering `ROA` at the password screen reports "Name not registered".

The same packed name also appears in the account slot at blob offset 2. The rest
of a slot is a 12-byte header, eight 20-byte recent-opponent records at `+0x0c`
(opponent names there are plain ASCII), and a variable-length tail at `+0xb0`
whose length is the halfword at blob offset 0.

Passwords are stored in the clear with the same codec as names. The field was
located by cribbing the user's own account (`A`, password `AAAA`) — four code-44
groups at bit 404 — and proven end to end: the password was changed to `1234` on
a copy, and the cabinet's PASSWORD path answered "Identity confirmed. Welcome
back." with the new one. All 440 records decode with no invalid codes; every CPU
account carries the placeholder `<PS>`. An earlier scan missed the field because
it capped codes at 45, rejecting `♪` and `V` — one player's password is `♪♪♪♪`.
The password lives only in the index record; the slot's packed name has no room
for it.

Field positions were fixed against the game's own screens rather than inferred:
the "Fight the enemy" list and the army ranking print each account's name with
its wins and losses, and all twelve accounts visible across two sessions matched
these positions exactly. An earlier attempt to crib against the built-in roster
at `0x49b5f0` failed — that roster is the static seed the card was created from,
and the live card has drifted away from it.

## Card layout (data pages, ECC stripped)

| Page | Contents |
| --- | --- |
| `0x00` | Card magic, plaintext |
| `0x10` | `BBLK` block header, plaintext |
| `0x20` | `MI` account index, blob of `0x831b` bytes (primary) |
| `0x70` | `MI` account index, second copy — differs from page `0x20` from byte 4 |
| `0xc0`+ | `PD` account slots, `0x2000` bytes each (`0x1ff8` blob after an 8-byte header) |

Pages `0x62..0x6f` and `0xb2..0xbf` are erased; everything from `0xc0` to the end
of the card is slot data.

The index plaintext is `0x24` bytes of header followed by 441 records of `0x4c`
bytes. Slot plaintext is `0x1fed` bytes: a 12-byte header, then 20-byte records
— a 4-byte stamp, four flag bytes, a marker byte, and a 12-byte area holding
NUL-separated strings.

On the user's card: both index copies decrypt cleanly, 161 of 1012 slots are in
use (slots 0..160), slot 161 carries a `PD` header but fails its trailer check,
and the rest is filler. Real player names come out readable — `SAMUEL`,
`BABYGIRL93`, `NAOMIRAMOS`, `KUZE`, `<GUEST>` and so on.

## Implemented tools

- `sc2edit.py`: `list`, `show`, `set`, `verify` — the only tool that writes.
- `sc2account.py`: the bit-packed account record and its name codec.
- `sc2crypto.py`: the stream cipher, keys, and trailer verification.
- `sc2cardfs.py`: `map`, `accounts`, `dump`, `strings` over a card image.
- `sc2card.py`: inspect, compare, strip ECC, unpack the PS2AC05 game blob.
- `lab.py`: drives the lab emulator — `launch`, `step`, `type`, `shot`, `dump`, `stop`.
- `pine.py`: PCSX2 PINE client for live EE reads/writes on localhost:28011.
- `ee_xrefs.py` / `ee_disasm.py`: MIPS xref and disassembly over a flat EE image.
- `extract_state.py`, `sc2profiles.py`, `scan_process_memory.py`,
  `research_runtime.py`, `research_xrefs.py`, `analyze_card_buffer.py`,
  `crypto_scan.py`: supporting analysis tools.

`PS2AC05.decompressed` is 3,777,412 bytes, SHA-256
`ed8f34df10e788d4cad14186aaee5da5a5e9486ae943f2805e4e009d25ed99a9`.

## Working with the lab emulator

Lab path: `research-data/lab-emulator`, launched as
`pcsx2-qt.exe "Soul Calibur 2 Lab.acgame"` with copied card/dongle/game files and
`EnablePINE = true`. Keyboard bindings: WASD directions, Z/X/C/V SC2 buttons,
5 coin, 1 start; PCSX2 hotkeys F8 screenshot, Space pause.

`lab.py step` is the reliable way to navigate: it unpauses the VM, sends an input
burst, takes a screenshot and pauses again. The Conquest menus run on 20–30
second timers that expire while a blind script is still thinking, which is what
derailed earlier attempts; single-stepping this way removes the race. `lab.py
type` walks the NAME ENTRY grid for a given string.

Route to the password screen, one `step` each: `coin,coin,start` → `up,horizontal`
(Conquest) → `right` (Password) → `horizontal` → NAME ENTRY.

PINE answers while the VM is paused, so `lab.py dump` captures all 32 MiB of EE
RAM in about 1.3 seconds at any point.

Useful EE addresses:

- `0x3c9354` — raw card page `0x20` read buffer (page `p` at `0x3c5354 + p*0x200`)
- `0x3b8440` — decrypted account index, as the game holds it
- `0x49b5f0` — the built-in canonical roster (demo names, 24-byte records)

## Findings from the running game

Entering `ROA` on the password screen returns "Name not registered", so the
built-in roster names are not accounts on this card. Ranking display records in
EE RAM have this layout relative to the `<NAME>` field: `+0x00` bracketed name,
`+0x10` ordinal placing, `+0x34` rank title, `+0x74` wins (LE32), `+0x78` losses
(LE32). The built-in roster uses 24-byte records: `+0x00..+0x03` rank/class/group/
flags, `+0x08..+0x13` bracketed name, `+0x14` character ID, `+0x15` wins, `+0x16`
losses, `+0x17` trailing flag.

## End-to-end write test (passed)

`sc2edit.py` set `<FALCATA>` to 777 wins / 42 losses and `<CURTANA>` to 999 wins
/ 1 loss on a copy of the card. The lab emulator booted from the edited card
without complaint, and the index it decrypted into its own memory at EE
`0x3b8448` read back:

- `<CURTANA>` W=999 L=1, `<FALCATA>` W=777 L=42 (the edited values)
- `<ROA>` W=48 L=26, `<KUDI>` W=32 L=20 (untouched, unchanged)

So the game accepts a re-encrypted card, its integrity checks pass, and it reads
exactly what was written. Changing one byte of plaintext re-randomises the whole
blob — the keystream is seeded from a checksum of the plaintext — so an edit
rewrites all 132 pages of both index copies. That is expected, not a bug.

A new account was also registered at the cabinet (name `B`, password `1234`) to
confirm the registration path, and a second (`ZQ9`) on the edited card. Neither
reached the card file: the game keeps the session in memory and had not written
the card by the time each run was stopped, so the on-card shape of a freshly
created account is still unobserved.

## Remaining work

1. Creating a brand-new account from the tool. Updating an existing record is
   solved, but writing a fresh one needs the on-card shape of a newly registered
   account — capture it by completing a full 8-battle session in the lab and
   letting the game write the card, then diffing.
2. The remaining index fields. Comparing the two index generations shows further
   mutable bytes at `0x36`-`0x38`, `0x3a`, `0x3d`, `0x44`-`0x45`; likely class,
   experience (the result screen shows `Exp`), army, character and timestamps.
3. The slot record's own fields (the recent-opponent list and the tail section).
4. Decode the other keyed blobs (`0x1ff8` at `0x1b05ec`, `0x76ce20`, `0x76cf00`).
5. Confirm the exact wins/losses field widths. Only 11 and 9 bits are ever used
   across all 440 records, and `sc2edit` limits writes to that, but the bits
   above are unused card-wide so the true field boundary is unproven.
