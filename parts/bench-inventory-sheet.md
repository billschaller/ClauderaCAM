# Bench inventory sheet — fill at the bench, hand back, crib files follow

Every row below becomes a line in a `parts/*.toml` crib file, written
from your answers **verbatim** (Article XI: the gate only sees what the
crib records, and the crib only records what the bench shows). Rules of
the sheet:

- Write what you can SEE or MEASURE. Leave a cell blank if the bench
  doesn't say — a blank is honest, a guess is a 14mm-drill incident.
- Counts are "on hand right now", not "what the listing said".
- Measurements marked 📏 want calipers; ⚡ wants the meter's diode mode.
- When it's done, hand the file back (edited in place is fine) and the
  crib entries + Board B layout freeze proceed from it.

## 1. ATtiny85 (SOIC-8) — blocks Board B layout freeze

| fact | your answer |
|---|---|
| quantity on hand | 4 |
| package confirm: SOIC-8, ~1.27 mm pin pitch (📏 if unsure) | SOIC-8 |
| marking on top (e.g. "ATTINY85-20SU") | ATTINY85-20SU|

## 2. THT 5 mm LEDs (the ring wants 12 of ONE colour + spares)

| fact | your answer |
|---|---|
| colours in the bin | various|
| count per colour (at least the best two) | 50|
| lead pitch confirm ~2.54 mm (📏) | 2.54|
| Vf per colour at meter test current (⚡, if your meter does it) |2.0-2.2 (red), 3.0-3.2 (blue), 3.0-3.2 (pink), 3.0-3.2 (white), 2.0-2.2 (yellow), 2.0-2.4 (yellow-green), 2.0-2.2 (orange), 3.2-3.4 (UV/purple)|
| body diameter confirm 5 mm (📏 one) | 5mm |

## 3. 6×6 tactile switches (S1, S2)

| fact | your answer |
|---|---|
| quantity on hand | loads - here's what I have: https://www.amazon.com/DaFuRui-250pcs-Tactile-Momentary-Assortment/dp/B07KGR7L9M - look for yourself|
| legs: 2 or 4? | |
| lead pitch(es) (📏 — 4-leg is usually 4.5 × 6.5) | |
| lead thickness (📏, sets the drill class) | |

## 4. Slide switch (SW1, SS-12D00-class SPDT)

| fact | your answer |
|---|---|
| quantity on hand | dozens |
| marking / model if printed | unknown, bigass SPDT |
| pin pitch (📏 — expect 2.54) | approx 4.86mm |
| pin cross-section (📏 flat blade w × t, sets the drill) | 1.4mm |

*note*: I also have some smaller dpdt 2.54 pitch switches

## 5. Buzzer BZ1 — Cylewet CYT1036 (identified from your Amazon link)

| fact | your answer |
|---|---|
| count on hand (listing says pack of 10) | 8 |
| body diameter (📏, listing-class ~12 mm) | 12mm |
| pin pitch (📏, expect ~7.6 mm) | ~7.6mm |
| "+" marking present on top? | yep |
| it BEEPS on plain 5 V DC (active confirm — clip leads, no resistor) | definitely yes |

## 6. Via wire (V1–V6 consumable)

| fact | your answer |
|---|---|
| what you'll use (22 AWG solid / clipped lead / other) | yes, lots of different types of wire|
| measured diameter (📏 — must pass a Ø1.0 hole) | don't worry about it |
| solid, not stranded? | yes |

*note*: The makera PCB fabrication kit comes with a bunch of via rivets ranging from 0.2mm to 1mm in size, but they look like a massive pain in the ass to use tbh.

## 7. Ø2 dowel pins (registration — confirms Decision Q11)

| fact | your answer |
|---|---|
| lengths in the tin (the plan assumes 2×12) | 2x12 |
| count of the 12 mm length | 20 |
| diameter confirm 2.0 (📏 one) | 2.0mm |

## 8. Double-sided blank stock

| fact | your answer |
|---|---|
| count of 150 × 100 blanks on hand | 10 |
| thickness (📏 — the job assumes 1.5) | 1.5 |
| FR-1 or FR-4 if the packaging says | FR-4 |
| copper both sides confirm | Yes |

## 9. 74HC595 (Board A-era question — the box photo and listing disagree)

| fact | your answer |
|---|---|
| is a 74HC595 physically in the 74HC box? | yes |
| quantity if yes | loads - I bought a whole box of these at one point - I have the plain DIP type but also a bunch of SN74HC595DWR SOIC-16 package type (10 of those) |
