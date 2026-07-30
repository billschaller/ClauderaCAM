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
| quantity on hand | |
| package confirm: SOIC-8, ~1.27 mm pin pitch (📏 if unsure) | |
| marking on top (e.g. "ATTINY85-20SU") | |

## 2. THT 5 mm LEDs (the ring wants 12 of ONE colour + spares)

| fact | your answer |
|---|---|
| colours in the bin | |
| count per colour (at least the best two) | |
| lead pitch confirm ~2.54 mm (📏) | |
| Vf per colour at meter test current (⚡, if your meter does it) | |
| body diameter confirm 5 mm (📏 one) | |

## 3. 6×6 tactile switches (S1, S2)

| fact | your answer |
|---|---|
| quantity on hand | |
| legs: 2 or 4? | |
| lead pitch(es) (📏 — 4-leg is usually 4.5 × 6.5) | |
| lead thickness (📏, sets the drill class) | |

## 4. Slide switch (SW1, SS-12D00-class SPDT)

| fact | your answer |
|---|---|
| quantity on hand | |
| marking / model if printed | |
| pin pitch (📏 — expect 2.54) | |
| pin cross-section (📏 flat blade w × t, sets the drill) | |

## 5. Buzzer BZ1 — Cylewet CYT1036 (identified from your Amazon link)

| fact | your answer |
|---|---|
| count on hand (listing says pack of 10) | |
| body diameter (📏, listing-class ~12 mm) | |
| pin pitch (📏, expect ~7.6 mm) | |
| "+" marking present on top? | |
| it BEEPS on plain 5 V DC (active confirm — clip leads, no resistor) | |

## 6. Via wire (V1–V6 consumable)

| fact | your answer |
|---|---|
| what you'll use (22 AWG solid / clipped lead / other) | |
| measured diameter (📏 — must pass a Ø1.0 hole) | |
| solid, not stranded? | |

## 7. Ø2 dowel pins (registration — confirms Decision Q11)

| fact | your answer |
|---|---|
| lengths in the tin (the plan assumes 2×12) | |
| count of the 12 mm length | |
| diameter confirm 2.0 (📏 one) | |

## 8. Double-sided blank stock

| fact | your answer |
|---|---|
| count of 150 × 100 blanks on hand | |
| thickness (📏 — the job assumes 1.5) | |
| FR-1 or FR-4 if the packaging says | |
| copper both sides confirm | |

## 9. 74HC595 (Board A-era question — the box photo and listing disagree)

| fact | your answer |
|---|---|
| is a 74HC595 physically in the 74HC box? | |
| quantity if yes | |
