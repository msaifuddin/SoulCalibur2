#!/usr/bin/env python3
"""Three Claude personas for the Conquest card.

    python claude_personas.py <card> [--output <card.edited>] [--wins-scale 1.0]

Each is a distinct style, authored move by move in numpad notation:

  <CLAUDE>     Astaroth — the wall. Slow, patient, punishes hard. Lives on
               66B and 6B at close range, throws everything within reach,
               2B+K on downed opponents. A tank you have to out-think.
  <CLAUDEAI>  Talim — the swarm. Constant pressure, never still: 66A, AA
               strings, RUN K, sidestep attacks. Weak per hit, relentless.
  CLAUDE.RE   Ivy — the spacer. Keeps you at mid range with 6B/3B pokes and
               66B, backs off to very far and whips 44A. Punishes approach.
               "RE" for reverse-engineering, since that is how she got here.

Passwords are set so the user can log in as each one. Rank/army stay whatever
the template persona had (see sc2persona).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import sc2card
import sc2cardfs
import sc2persona as P
from sc2persona import Style

ASTAROTH, TALIM, IVY = 0x12, 0x16, 0x0B

PERSONAS = [
    dict(
        name="<CLAUDE>", password="CLD1", template="<KENTON♪>", wins=88, losses=31,
        style=Style(
            character=ASTAROTH,
            ratios=(0.55, 0.62, 0.30, 0.48),  # Soul, Power, Skill, Wisdom
            prefs=[
                ("point-blank", "neutral", "BG", 180), ("point-blank", "neutral", "AG", 120),
                ("point-blank", "neutral", "6B", 90), ("point-blank", "neutral", "2K", 60),
                ("close", "neutral", "66B", 200), ("close", "neutral", "6B", 160),
                ("close", "neutral", "3B", 120), ("close", "neutral", "1B", 80),
                ("close", "neutral", "44B", 60), ("close", "neutral", "BG", 70),
                ("mid-close", "neutral", "66B", 180), ("mid-close", "neutral", "RUN6B", 120),
                ("mid-close", "neutral", "3B", 90), ("mid-close", "neutral", "44A", 50),
                ("mid", "neutral", "RUN6B", 160), ("mid", "neutral", "66B", 120),
                ("mid", "neutral", "66A", 60),
                ("far", "neutral", "RUN6B", 120), ("far", "neutral", "66B", 80),
                ("close", "walking", "3B", 140), ("close", "walking", "66B", 100),
                ("point-blank", "downed", "2BK", 200), ("point-blank", "downed", "1B", 120),
                ("close", "downed", "2BK", 180), ("close", "downed", "3B", 100),
                ("point-blank", "air", "BG", 160), ("point-blank", "air", "3B", 90),
            ],
        ),
    ),
    dict(
        name="<CLAUDEAI>", password="CLD2", template="<TANY>", wins=104, losses=47,
        style=Style(
            character=TALIM,
            ratios=(0.72, 0.28, 0.66, 0.35),
            prefs=[
                ("point-blank", "neutral", "A_A", 200), ("point-blank", "neutral", "A_B", 140),
                ("point-blank", "neutral", "2K", 120), ("point-blank", "neutral", "6K", 90),
                ("point-blank", "neutral", "3K", 80), ("point-blank", "neutral", "BK", 70),
                ("close", "neutral", "66A", 200), ("close", "neutral", "A_A", 160),
                ("close", "neutral", "6B", 120), ("close", "neutral", "66K", 110),
                ("close", "neutral", "3AB", 90), ("close", "neutral", "44K", 70),
                ("mid-close", "neutral", "66A", 180), ("mid-close", "neutral", "RUN6K", 150),
                ("mid-close", "neutral", "66B", 100), ("mid-close", "neutral", "RUN3A", 80),
                ("mid", "neutral", "RUN6K", 160), ("mid", "neutral", "66A", 140),
                ("mid", "neutral", "RUN2K", 70),
                ("far", "neutral", "RUN6K", 150), ("far", "neutral", "RUN3A", 100),
                ("very-far", "neutral", "RUN6K", 120),
                ("close", "walking", "66A", 160), ("close", "walking", "A_A", 120),
                ("point-blank", "downed", "2K", 160), ("point-blank", "downed", "3B", 120),
                ("close", "downed", "66A", 140), ("close", "downed", "2KF", 100),
                ("point-blank", "air", "A_A", 140), ("point-blank", "air", "6K", 100),
            ],
        ),
    ),
    dict(
        name="CLAUDE.RE", password="CLD3", template="<SHIRON>", wins=76, losses=22,
        style=Style(
            character=IVY,
            ratios=(0.40, 0.45, 0.70, 0.78),
            prefs=[
                ("point-blank", "neutral", "BG", 120), ("point-blank", "neutral", "2A", 100),
                ("point-blank", "neutral", "4B", 90), ("point-blank", "neutral", "44B", 80),
                ("close", "neutral", "6B", 160), ("close", "neutral", "3B", 140),
                ("close", "neutral", "44A", 110), ("close", "neutral", "4B", 90),
                ("close", "neutral", "66B", 80),
                ("mid-close", "neutral", "6B", 200), ("mid-close", "neutral", "3B", 160),
                ("mid-close", "neutral", "66B", 120), ("mid-close", "neutral", "6A", 90),
                ("mid", "neutral", "66B", 180), ("mid", "neutral", "6B", 150),
                ("mid", "neutral", "44A", 120), ("mid", "neutral", "3AB", 70),
                ("far", "neutral", "44A", 200), ("far", "neutral", "66B", 120),
                ("far", "neutral", "RUN6B", 80),
                ("very-far", "neutral", "44A", 200), ("very-far", "neutral", "66AB", 100),
                ("mid", "walking", "3B", 160), ("mid", "walking", "66B", 120),
                ("close", "walking", "6B", 140),
                ("point-blank", "downed", "2A", 140), ("close", "downed", "3B", 120),
                ("mid", "downed", "44A", 120), ("close", "downed", "2BK", 100),
                ("point-blank", "air", "3B", 150), ("close", "air", "6B", 100),
            ],
        ),
    ),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("card", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    raw = args.card.read_bytes()
    data = sc2cardfs.card_data(args.card)
    for persona in PERSONAS:
        data, position, displaced = P.create_persona(
            data, persona["template"], persona["name"], persona["password"],
            persona["style"], persona["wins"], persona["losses"],
        )
        note = f"  (log entry {displaced!r} fell off the table)" if displaced else ""
        print(f"created {persona['name']:<12} as {P.CHARACTERS[persona['style'].character]:<9} "
              f"record {position}  password {persona['password']}{note}")

    rebuilt = sc2card.rebuild_card(data, raw)
    output = args.output or args.card.with_suffix(args.card.suffix + ".edited")
    output.write_bytes(rebuilt)
    bad = sc2card.check_spare(rebuilt)
    print(f"wrote {output}  (stale check values: {len(bad)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
