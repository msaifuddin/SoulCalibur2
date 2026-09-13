# Sample card

`Card0.conquestcard` — a raw dump of a real Conquest card from a working
System 246 cabinet (Conquest ID 7678, Ver. D), published with the owner's
consent so the tools can be exercised without hardware.

- 8,650,752 bytes: 16,384 pages × (512 data + 16 spare)
- SHA-256 `30a11b69eeb00158d6c1e838431e8d2c23143f2db5e7821f4427bd8b45017357`
- 161 account slots in use, 440 index records (about 40 are the game's own
  CPU opponents, with `<BRACKETED>` names)

```
python ..\sc2edit.py list Card0.conquestcard --player-only
python ..\sc2edit.py verify Card0.conquestcard
```
