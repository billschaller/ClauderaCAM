# Board A "coupon" — tool pull-list (machine side)

The four programs (blessed bytes in `tests/golden_pcb/`, regenerated
identically into `out/`) call for **four crib bits plus the laser
module**. Labels are the printed bit-case labels; qty is the crib count
(`jobs/inventory.toml`).

| slot | pull from the crib | geometry | used in | spindle |
|---|---|---|---|---|
| T2 | "0.2mm Tip Engraving Metal (30°)" · qty 7 | vee 30°, tip 0.2, FL 10, 1F, 3.175 shank | **A mill**: pcb-iso (multi-pass ladder) | S12000 |
| T3 | "6mm Flute Corn 0.8" · qty 3 | flat Ø0.8, 2F, FL 6, 3.175 shank | **A mill**: pcb-clear · **D holes**: every bore (helical) | S12000 |
| T5 | "UV Solder Mask Removal Tool (spring)" · qty 2 | spring scrub, 0.3 width, FL 2, 3.175 shank | **C scrub**: Z−0.21 is spring PRELOAD, not cut depth | S6000 |
| T7 | "6mm Flute Corn 1.0" · qty 4 | flat Ø1.0, 2F, FL 6, 3.175 shank | **D holes**: edge cutout + 4 tabs | S12000 |
| — | 455 nm laser module | replaces the spindle for program B only | **B silk**: dose S0.03 F100; Z0 = focal plane after M321; M323 test-fire on scrap first | — |

Tool changes inside a program: **A** has one M6 (T2 → T3), **D** has one
(T3 → T7); B and C are single-tool. The spindle stops before every
change — the programs enforce it.

Bench consumables for the run: full-bed tape, UV solder mask + squeegee
+ UV cure lamp, white coat, IPA (wipes the uncured white after silk).
No dowel pins — Board A is single-sided.

Each program's header repeats its own tool line (`head -8 <program>.nc`)
and the viewer's run-sheet card carries the same list interleaved with
the operator steps. Assembly-side counterpart: `ASSEMBLY.md` +
`assembly-map.png`.
