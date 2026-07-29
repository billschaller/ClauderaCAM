# The parts crib

Article XI, applied to components: a part the bench does not hold does
not enter a schematic. These files are the machine-checkable inventory
the PCB parts gate reads at layout freeze.

Each file carries a `status`:

- `listing-transcribed` — sourced from the seller's listing images
  (method below), good enough to DESIGN against, not yet proof the
  physical book matches. Verify the exact pulled value against the
  physical book when populating a BOM.
- `bench-verified` — the operator has checked the physical kit against
  the file.

## EGSCST assortment books (0603 / 0805 / 1206)

Transcribed 2026-07-29 from the Amazon listing gallery images
(B0F43WGTCR / B0DZXQPFHP / B0F1WSZC83). Method, for next time: the
listing HTML is served gzip-compressed and bot-walled to summarizing
fetchers — `curl --compressed` with a browser User-Agent returns the
full page; the complete value tables are printed in the gallery images
(`"hiRes"` URLs in the page JSON), which read cleanly at 1500px. Three
parallel agents transcribed and cross-checked master table vs pouch
labels.

All three books share the same roster: 170 E24 resistors (0Ω–10MΩ, 1%,
~25/value), 9 diodes, 16 SOT-23 transistors, 6 IC part numbers, 15
inductors (10nH–10µH), 5 LED colors. Only the CAPACITOR lists differ —
and it matters:

| book | capacitor range | the useful truth |
|---|---|---|
| 0603 | 120pF–1µF | mid values incl. 47nF, 1µF |
| 0805 | 22pF–10µF | THE decoupling/bulk book: only source of 100nF, 150nF, 220nF, 470nF, 680nF, 10µF; also the only C0G (100pF) |
| 1206 | 120pF–10nF | small values only |

Seller-side discrepancies recorded in the files (do not "fix" them —
they are what the pouches say): a "300pF" pouch coded 331K, 1N4148
marking printed T4 (table) vs T7 (pouch), an S8550 pouch misprinted
S8050, covers claim "5 IC values / 55 pcs" while six part numbers are
listed, and per-value quantities are derived from category totals, not
printed.

## Not yet cataloged (bench-catalog before their first BOM)

- KOKISO 500pc SMD kit (SOT-23 diodes / transistors / MOSFETs)
- BOJACK 12-piece DIP IC kit (known to include NE555)
- 74HC-series DIP kit
- THT bins (resistors, capacitors, inductors, diodes, buttons,
  switches, piezos, LEDs)
- ESP32-H2-Zero modules, SMD ATtiny85 (SOIC-8)
