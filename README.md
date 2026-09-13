# SoulCalibur II Conquest Card Reader & Editor

Read and edit the player database on the custom memory card used by the
**System 246 arcade** version of SoulCalibur II (Conquest mode).

The card is not a PS2 filesystem. Everything past its header pages is encrypted
with a small stream cipher that lives in the game's own EE code, every page
carries a non-standard check value, and the player records inside are
bit-packed. All of that is reversed and implemented here, verified against the
running game, so a card can be read and edited offline:

```
$ python sc2edit.py list samples/Card0.conquestcard --player-only --by-wins
  idx  name           wins   losses
    40  A              168      8
    55  OMAR           113     92
    57  DR. KVADER      25     69
    48  DA-HA           22     33
```

```
$ python sc2edit.py set samples/Card0.conquestcard FALCATA --wins 120 --losses 30
wrote samples/Card0.conquestcard.edited
  before    27  <FALCATA>       32W    13L
  after     27  <FALCATA>      120W    30L
```

Names, passwords, wins and losses can be read and written. Edited cards load in
the game and the game reads back exactly what was written — including logging
in with a changed password.

## Background

The Conquest card has been called
["the only thing on System 246/256 that cannot be duplicated, reset or remade"](https://www.arcade-projects.com/threads/the-only-thing-on-system-246-256-that-cannot-be-currently-duplicated-reset-remade-the-soul-calibur-ii-conquest-mode-memory-card.22280/).
[Matias Israelson (israpps)](https://github.com/israpps) reversed the card's
transport and format in
[Cracking the SoulCalibur II Conquest Memory card](https://israpps.github.io/blog/understanding-sc2-memorycard),
built a tool to unpack the game binary, and showed how to make a blank Conquest
card from a retail one. This project starts where that work stops: what the
game actually *stores* on the card, and how to change it.

## What was found

**One cipher, two keys.** The card's player database and the `PS2AC05` game
blob on the dongle use the same stream cipher: a 16-bit LCG
(`state = rotr16(state,1) * 5 + 1`) XORed byte-wise, seeded from a checksum of
the *plaintext* that is stashed in a 2-byte trailer, followed by an encrypted
9-byte closing string. Keys are three 16-bit halfwords plus that string:

| Blob | mask | seed | factor | string |
| --- | --- | --- | --- | --- |
| Conquest card | `0x545e` | `0x0276` | `0x0512` | `TSEUQNOC` |
| `PS2AC05` game blob | `0xebd7` | `0xa21f` | `0x000d` | `VersionA` |

The encryptor is at EE `0x216cb8`, the decryptor at `0x216e08`. Because the
keystream is seeded from the plaintext, changing one byte re-randomises the
whole blob; the trailer doubles as a free integrity check.

**The page check value.** Each 512-byte page carries 4 bytes in its spare area:
CRC-32/MPEG-2 (`0x04c11db7`, init 0, no reflection, no xor-out), big-endian,
`0xff`-padded. Not the standard PS2 Hamming ECC.

**Card layout** (data pages, spare stripped):

| Page | Contents |
| --- | --- |
| `0x00` | Card magic, plaintext |
| `0x10` | `BBLK` block header |
| `0x20` | `MI` account index — 0x831b-byte blob, 441 records of 0x4c bytes |
| `0x70` | Second copy of the index (a separate generation) |
| `0xc0`+ | `PD` account slots, 0x2000 bytes each |

**The account record** is bit-packed, read LSB-first through the game's bit
reader at EE `0x1b1410`:

| Field | Start bit | Layout |
| --- | --- | --- |
| name | 336 | 4-bit length, then 6-bit glyph codes (64-bit field) |
| password | 400 | 4-bit length, then four 6-bit glyph codes |
| wins | 487 | 11 bits used |
| losses | 512 | 9 bits used |

Glyph codes index the game's own table at EE `0x49b9a0`:
`7H!<N4J'DY06SPQO2M 3.XBTZ:U-5R9&CK>FW?GL8EI1A♪V`. That is why a name never
appears as ASCII on the card. The game's CPU opponents have bracketed names
(`<FALCATA>`); names registered at the cabinet do not.

## How the CPU opponents work

Every account slot also carries an 8 KB **play-style profile**: four ratios
that match the game's Analysis meters, some aggregates, and a histogram of
*(move, situation) → count*. The layout is an exact fit for the slot tail:
`0x2c + 8 × 0x1f0 × 2 = 0x1f2c`.

In Conquest the CPU chooses moves from that histogram. The game classifies
the moment into a situation id — distance class in bits 0-2, opponent state
in bits 3-5, with the developers' own labels still in the binary (至近距離
point-blank … 超遠距離 very far; 対歩き walking, 対ダウン downed, 対AIR
airborne) — filters the character's command table for the range, weights each
candidate by its count in that situation, and draws by cumulative weight.
Difficulty comes from a rank → level table (Newcomer 2 … Edge Master 11) that
selects base parameters, blended with the profile.

Two things fell out of verifying this live:

- The bracketed personas (`<FALCATA>` and friends) are **static ghosts
  authored by Namco**: every copy of a persona on the card carries a
  byte-identical histogram, and it never changes during a fight.
- **Human accounts are the live ghosts.** The recorder fills your histogram as
  you play, and when another player draws you as an opponent the game fights
  as you, with your recorded preferences.

Move indices resolve to names in numpad notation (`cmd_12_66B`,
`cmd_12_RUN6B`), so a persona's style reads directly: `<KENTON♪>` is an
Astaroth who lives on 66B and running 6B at close range and punishes downed
opponents with 2B+K. Addresses and the full trace are in
[PROGRESS.md](PROGRESS.md).

## How it was verified

Every claim was checked against the game rather than assumed:

- `sc2crypto.decrypt` reproduces the game's own in-RAM plaintext byte for byte,
  and re-derives israpps' `PS2AC05.decrypted` exactly.
- The page check value matches all 16,329 written pages of the sample card; an
  unedited card rebuilds to identical bytes.
- Wins and losses were located with a bit-level search against the game's
  "Fight the enemy" and army-ranking screens: twelve accounts, all exact.
- The password field was located with a known account/password pair, then
  proven by changing it on a copy and logging in at the cabinet with the new
  one: *"Identity confirmed. Welcome back."*
- An edited card was booted in the emulator and the game decrypted it into its
  own memory showing the edited values.
- The CPU model was checked against the running game: the profile buffers
  were found at the predicted addresses in battle order, and a persona's move
  choices were watched live over PINE, move by move, against its histogram.

A wrong turn worth recording: the first attempt cribbed field positions against
the built-in demo roster in the game binary. It gave 73/86 on wins and noise on
losses. That roster is the static seed the card was created from; a live card
has drifted years away from it. Only the game's own screens are ground truth.

## Tools

| Tool | Purpose |
| --- | --- |
| `sc2edit.py` | Read and update player stats. The one tool that writes. |
| `sc2account.py` | The bit-packed account record: name/password codec, wins, losses. |
| `sc2crypto.py` | The card/game stream cipher: `decrypt`, `encrypt`, `verify`. |
| `sc2cardfs.py` | Card layout: account index, account slots, decrypted dumps. |
| `sc2card.py` | Image validation, page diffing, spare/EDC, `PS2AC05` unpacking. |
| `lab.py` | Drives an isolated PCSX2 instance: launch, input, screenshot, EE dump. |
| `pine.py` | PCSX2 PINE client for live EE memory reads and writes. |
| `ee_xrefs.py`, `ee_disasm.py` | MIPS cross-reference and disassembly over a flat EE RAM image. |
| `sc2profiles.py` | Finds decoded roster/ranking records in EE RAM. |

`ee_disasm.py` emits R5900-only opcodes that Capstone cannot decode as raw
words and keeps going, rather than truncating a listing at the first one.

### Editing safely

`sc2edit.py set` never overwrites its input. It writes to `--output` (default
`<card>.edited`), keeps a one-time `.bak` of the original, and re-reads the
finished image before keeping it — the blob trailers, every page check value
and the edited fields all have to come back exactly as intended, or nothing is
written. `--dry-run` verifies an edit without producing a file.

```
python sc2edit.py show   Card0.conquestcard A
python sc2edit.py set    Card0.conquestcard A --password 1234
python sc2edit.py set    Card0.conquestcard 40 --rename NEWNAME --wins 0 --losses 0
python sc2edit.py verify Card0.conquestcard
```

Fields: `--wins` 0-2047, `--losses` 0-511, `--password` 1-4 characters,
`--rename` up to 10 characters, all from the grid above. Accounts are addressed
by name or by the index number `list` prints.

### Inspecting

```
python sc2cardfs.py map      Card0.conquestcard
python sc2cardfs.py accounts Card0.conquestcard
python sc2cardfs.py dump     Card0.conquestcard slot3.bin --slot 3
python sc2card.py   inspect  Card0.conquestcard
python sc2card.py   compare  old.conquestcard new.conquestcard
```

## Sample data

`samples/Card0.conquestcard` is a real card image from a working cabinet
(Conquest ID 7678, 161 registered accounts), published with the owner's consent
so the tools can be tried without hardware. It is a raw 8,650,752-byte dump:
16,384 pages of 512 data + 16 spare bytes.

Not included, and not needed for reading cards: the `PS2AC05` game binary, the
game disc image and the dongle image. Those are Namco's.

## Requirements

Python 3.11+. The editing and inspection tools have no dependencies. The
analysis tools need:

```
pip install capstone zstandard
```

`lab.py` and `pine.py` additionally need a PCSX2 build with arcade (System
246) support and PINE enabled; the lab was run on
[pcsx2x6](https://github.com/pcsx2x6), the PCSX2 fork with System 246/256
support.

## Method

Static analysis alone did not crack this. The approach that worked was a loop
between the two:

1. Run the game in an isolated PCSX2 with PINE enabled and drive it with
   `lab.py`, which unpauses the VM only for the duration of an input burst.
   Conquest menus run on 20–30 second timers, and single-stepping removes the
   race.
2. Dump all 32 MiB of EE RAM (`lab.py dump`, ~1.3 s) at interesting moments.
3. Cross-reference addresses in the flat RAM image (`ee_xrefs.py`). A file
   offset in the dump *is* the EE address, which sidesteps having to reconstruct
   how the game's segments were relocated.
4. Disassemble what the cross-references point at (`ee_disasm.py`).
5. Take what the game shows on screen as ground truth and search the decrypted
   data for it at bit granularity.

The full research log, including dead ends, is in [PROGRESS.md](PROGRESS.md).

## Credits

- **[Matias Israelson (israpps)](https://github.com/israpps)** — reversed the
  Conquest card's SIO2 transport and the DONGLEMAN driver, documented the card
  in [Cracking the SoulCalibur II Conquest Memory card](https://israpps.github.io/blog/understanding-sc2-memorycard),
  wrote [SoulCalibur2-game-unpacker](https://github.com/israpps/SoulCalibur2-game-unpacker)
  (from which `sc2card.py unpack-game` was ported) and SC2MAKER. This project
  would not exist without that groundwork: the unpacked `PS2AC05` was the
  starting point for every address above.
- **[pcsx2x6](https://github.com/pcsx2x6)** — the PCSX2 fork with System
  246/256 support that the lab runs on, and PCSX2's PINE protocol for live
  memory access.
- **[mymc+](https://github.com/thestr4ng3r/mymcplus)** by Ross Ridge and
  Florian Märkl — the PS2 memory card reference that established the card's
  spare area was *not* standard ECC.
- **[Capstone](https://www.capstone-engine.org/)** — MIPS disassembly.
- The [Arcade-Projects](https://www.arcade-projects.com/) community for keeping
  the card's story alive.

Reverse engineering and tooling by [msaifuddin](https://github.com/msaifuddin)
with Claude (Anthropic).

## License

MIT — see [LICENSE](LICENSE). SoulCalibur II is © Namco; no game assets are
included.
