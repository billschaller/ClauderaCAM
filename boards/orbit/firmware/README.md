# orbit — Board B firmware

ATtiny85 firmware for the ClauderaCAM "orbit" board: a 12-LED charlieplexed
ring, two buttons that live on the matrix lines, and an active buzzer. Bare
metal C against `avr/io.h` registers — no Arduino core, no Arduino API, no
sketch. `arduino-cli` appears here only as the package manager that puts a
known-good AVR toolchain on disk.

The contract is `boards/orbit/SPEC.md` (sections *Circuit*, *Pin budget*,
*Firmware*, *Assembly*). Every block in `orbit.c` cites the constraint it
serves. Where the SPEC is silent, the code takes the conservative reading and
says so in a comment — grep for `AMBIGUITY`, and see *Open readings* below.

## Files

| file | what it is |
|---|---|
| `orbit.c` | the firmware (534 lines; **302 lines of code** once comments and blanks are stripped — see *Size* on why that is 2× the SPEC's "~150 lines") |
| `matrix.h` | **the ring→charlieplex pair table** + the compile-time proof it is legal. The only file a layout permutation touches |
| `build.sh` | host checks, then both AVR builds, with the exact bundled-toolchain paths |
| `matrix_check.c` | host harness: re-proves the matrix properties at runtime, by construction |
| `orbit_test.c`, `test/` | host logic simulation — compiles the real `orbit.c` against mocked AVR registers |
| `orbit.hex` | **flash this** (digital button read, the default) |
| `orbit-adc.hex` | the ADC-threshold fallback build (see *Escalation path*) |

## Build

```
./build.sh
```

It runs the host checks first (a bad matrix must fail cheaply), then both AVR
builds. Exact commands, as executed:

```
cc -std=c11 -Wall -Wextra -Werror matrix_check.c -o matrix_check && ./matrix_check
cc -std=c11 -I. -Itest -Wall -Wextra -Werror orbit_test.c -o orbit_test && ./orbit_test

AVR=~/.arduino15/packages/arduino/tools/avr-gcc/7.3.0-atmel3.6.1-arduino7/bin

$AVR/avr-gcc -mmcu=attiny85 -DF_CPU=8000000UL -Os -std=gnu11 -Wall -Wextra -Werror \
  -funsigned-char -funsigned-bitfields -fpack-struct -fshort-enums \
  -ffunction-sections -fdata-sections -DORBIT_BUTTON_ADC=0 -c orbit.c -o orbit.o
$AVR/avr-gcc -mmcu=attiny85 -Wl,--gc-sections -o orbit.elf orbit.o -Wl,-Map,orbit.map
$AVR/avr-objcopy -O ihex -R .eeprom orbit.elf orbit.hex
$AVR/avr-size --mcu=attiny85 -C orbit.elf
```

`-Werror` is deliberate: the SPEC asks for a clean `-Wall -Wextra` build, so
the build enforces it rather than reporting it.

### Recreating the toolchain

Installed user-locally, no sudo, into `./bin` + `~/.arduino15`:

```
curl -sSL -o acli.tgz https://downloads.arduino.cc/arduino-cli/arduino-cli_latest_Linux_64bit.tar.gz
mkdir -p bin && tar xzf acli.tgz -C bin arduino-cli
./bin/arduino-cli core update-index
./bin/arduino-cli core install arduino:avr
```

Versions used here: arduino-cli 1.5.2-rc.1, `arduino:avr@1.8.8`,
avr-gcc 7.3.0-atmel3.6.1-arduino7, avrdude 8.0.0-arduino1.

## Size

```
Program:  1510 bytes (18.4% of 8 KB flash)
Data:       17 bytes ( 3.3% of 512 B RAM)
```

RAM is 17 bytes of `.bss`, zero `.data`. Every constant table — the matrix and
all five beep patterns — is `PROGMEM`, so it costs flash only. Call depth is
shallow (main → one state function → `blink`/`wait_ms`) with a 4-level ISR
nest of nothing, so the ~495 B of free RAM is all stack. **Both SPEC limits
are met with large margin: 6.5 KB flash and ~495 B RAM spare.**

The ADC fallback build is 1534 bytes / 17 bytes.

### Source size vs the SPEC's "~150 lines AVR C"

`orbit.c` is 302 code lines, roughly 2× the SPEC's estimate. Nothing was
invented to get there; the overage is itemised so it can be judged:

| lines | what | why it is not optional |
|---:|---|---|
| ~35 | the ADC escalation path (`ORBIT_BUTTON_ADC`) | the SPEC requires the fallback to be designed in, and both branches are counted |
| ~30 | `orbit_sleep()` | requirement 4 in full: pin-park, PCINT arm, wake, re-arm |
| ~25 | `ms_now` / `frame_set` / `btn_take` atomics | 16-bit state shared with a 1 kHz ISR; without them the tearing is real |
| ~25 | the buzzer pattern sequencer | five patterns off Timer0's tick, as the SPEC specifies |
| ~20 | the debounce state machine | "Debounce is firmware" — there is no hardware to lean on |

The remaining ~165 lines are the display ISR, the self-test, attract and the
round — which is the SPEC's estimate. The estimate was for the game; the
delivered file is the game plus the fallback, the sleep path and the
concurrency correctness.

## Fuses

Internal 8 MHz RC, CKDIV8 **cleared**, SPIEN **enabled**, RSTDISBL **never**
programmed (SPEC assembly step 12; a fused-off RESET on a board with no
connector and no HV programmer is a brick).

| fuse | value | meaning |
|---|---|---|
| `lfuse` | **0xE2** | CKSEL=0010 internal 8 MHz RC; SUT=10 default start-up; CKOUT unprogrammed; **CKDIV8 unprogrammed** → the part runs at 8 MHz, not 1 MHz |
| `hfuse` | **0xDF** | RSTDISBL unprogrammed (PB5 stays RESET); DWEN unprogrammed; **SPIEN programmed** (ISP alive); WDTON unprogrammed; EESAVE unprogrammed; BODLEVEL=111 |
| `efuse` | **0xFF** | SELFPRGEN unprogrammed |

Brown-out is left **disabled** on purpose: the SPEC's power budget claims
`Sleep … MCU <1 µA`, and an enabled BOD burns ~20 µA continuously in
power-down, which would blow that number by 20×. The firmware writes no
EEPROM, so the usual reason to want BOD does not apply here.

A factory ATtiny85 ships with `lfuse 0x62` (CKDIV8 programmed → 1 MHz), so the
**fuse write must come first** — see the `-B` note below.

```
avrdude -c usbasp -p attiny85 -B 8 \
        -U lfuse:w:0xE2:m -U hfuse:w:0xDF:m -U efuse:w:0xFF:m
```

`-B 8` (≈125 kHz ISP clock) is required for the *first* connection: ISP SCK
must stay under ¼ of the target clock, and a factory part is running at
1 MHz, where a usbasp's default 375 kHz will not sync. After the lfuse is
written the part is at 8 MHz and `-B 1` is fine.

`orbit.c` also forces the runtime clock prescaler to /1 (`CLKPR`) at boot.
That is a **belt, not a replacement** for the fuse — it rescues a chip that
was flashed before its fuses were written, but CKDIV8 still governs the clock
during ISP itself, so the fuse write remains mandatory.

## Flashing through the ISP pads

The board has six bare copper pads on B.Cu in the standard 2×3 AVR ISP grid
at 2.54 mm (MISO/VCC, SCK/MOSI, RST/GND), pin 1 marked with a square silk
tick, all six labelled. There is no connector — pogo-press or tack-solder
wires (SPEC *ISP — bare pads*).

1. Power the board from PAD+/PAD− at 5 V, or let the programmer supply VCC —
   **not both**. SW1 must be ON.
2. Press/solder the six lines to the pads, pin 1 to the square tick.
3. Fuses first (command above), then:

```
avrdude -c usbasp -p attiny85 -B 1 -U flash:w:orbit.hex:i
```

4. Verify (avrdude verifies by default; `-U flash:v:orbit.hex:i` re-reads).

Expect the ring to **flicker while flashing** — MOSI/MISO/SCK *are*
charlieplex lines L0/L1/L2, so the programmer lights up to two LEDs through
560 Ω at ≈6 mA. The SPEC calls this a feature: it says the matrix is alive
before any firmware runs. A button held during programming just adds 4.7 kΩ
to GND on PB2/PB3 and is harmless.

If the programmer stumbles on the RESET RC, **lift C4** (10 nF) — it is the
one part on the board specified to be removable.

## What the boot self-test proves

On every power-up, before anything else, the firmware walks ring positions
1→12 one at a time (120 ms each, ~1.4 s total), then chirps once.

That walk is the board's **electrical continuity test** (SPEC assembly step
13). Because `matrix.h` statically asserts that the 12 entries are the
complete, distinct set of 4×3 ordered (anode, cathode) pairs, walking the ring
energises **every legal combination of the four lines**. So the walk exercises:

- every LED and its orientation,
- every one of the 12 series resistors,
- every wire via in the six two-track ring corridors,
- all four line nets end to end.

**A position that stays dark accuses exactly three named places**: that LED
(or its polarity), its series resistor, or the vias/traces feeding its
corridor. A whole *sector* dark (two adjacent positions) points at the shared
corridor rather than at either LED. The chirp at the end proves PB4 → R14 →
Q2 → BZ1 and the BAV99 clamp node.

The self-test runs unconditionally and cannot be skipped — it is the board's
acceptance test, not a debug mode.

## Transcribing MATRIX.md

The ring→pair mapping is a **layout degree of freedom** (SPEC *The ring*, and
Decision 5 "trial then permute"). The table in `matrix.h` is marked
**PROVISIONAL** and currently holds the SPEC's default matrix. When
`boards/orbit/MATRIX.md` lands:

1. Open `matrix.h`, find `ORBIT_RING_MAP`.
2. Rewrite its twelve `X(position, anode, cathode)` rows from MATRIX.md.
   Lines are numbered by port bit: **L0=PB0, L1=PB1, L2=PB2, L3=PB3**.
3. Rebuild. Change nothing else — the table is the only place the mapping
   appears; the ISR, the game and the self-test all read it.
4. Delete the PROVISIONAL banner at the top of `matrix.h`, replacing it with
   the MATRIX.md revision the table was transcribed from.

Row order in the file **does not matter**. The array is built with designated
initialisers indexed by ring position, so `X(7, …)` defines position 7 wherever
it is written — a mis-ordered transcription cannot silently rotate the ring.

A bad transcription is a **build failure, not a dark LED found on the bench**.
`matrix.h` static-asserts that the rows name lines 0–3, that no row is a
self-pair, that there are exactly 12 of them, that they cover positions 1–12
exactly once, and that the 12 (anode, cathode) pairs are distinct and cover
the whole 4×3 ordered set. All five asserts were verified to fire — see
*Verification*.

## Escalation path: ADC button read

Default is the digital blanking-window read (SPEC Decision 3). If the digital
margin disappoints on the bench, rebuild with `-DORBIT_BUTTON_ADC=1` (already
built as `orbit-adc.hex`) and flash that instead. **No hardware change**: same
pins, same internal pull-up, same blanking window — only the comparison
changes, from the input buffer's VIL to an 8-bit threshold at 0.5·VCC on
ADC3 (PB3) / ADC1 (PB2). The ADC runs at 500 kHz so the conversion fits the
blanking window; that is above the 200 kHz full-10-bit window on purpose,
since this read is a threshold, not a measurement. `DIDR0` is deliberately
left alone — disabling the digital input buffer would also kill the PCINT
wake path.

## How to play

- **Power up** → self-test walk, chirp.
- **Attract**: a dot orbits slowly (150 ms/step) while the marker at position 1
  blinks at 350 ms, so the marker is never mistaken for the dot. The marker is
  silk, not a part — the firmware blinking it is its only other marking.
- **S2 (START)** begins a round. The dot starts opposite the marker and steps
  every 220 ms.
- **S1 (CATCH)** scores against position 1:
  - **exact hit** → 5-pulse win beep, marker blinks 3×, the step interval
    shrinks ×0.8 (floor 45 ms) and the round continues;
  - **near miss** (position 2 or 12) → single 150 ms beep, the dot's own
    position blinks twice, round over;
  - **miss** → 450 ms buzz, the whole ring blinks 3×, round over.
- **Idle 30 s** (in attract or mid-round) → power-down sleep, all lines
  input-pullup, buzzer gate held low, PCINT armed on PB2/PB3. Any press wakes
  it; the waking press is consumed, and wake resumes **attract**, never a
  round.

## Verification

Nothing below is a claim about silicon. These are host-level facts:

- **Both AVR builds compile and link with zero warnings** at
  `-Os -Wall -Wextra -Werror`.
- **Register constants checked in the disassembly**, not just in the source:
  `TCCR0A=0x02` (CTC), `TCCR0B=0x03` (clk/64), `OCR0A=124` → 8 MHz/64/125 =
  **exactly 1000 Hz** slot rate, 250 Hz frame rate; `TIMSK` bit OCIE0A set;
  vector 10 (TIMER0_COMPA) and vector 2 (PCINT0) both populated.
- **Matrix static asserts fire.** Six corrupted tables were built as negative
  controls — duplicate pair, self-pair, out-of-range line, duplicate position,
  dropped row, swapped polarity — and each failed the build on the intended
  assert.
- **Host logic simulation, 26 checks, all passing** (`orbit_test.c`). It
  compiles the real `orbit.c` against mocked registers and checks the drive
  pattern against an *independently written* LED model over **all 4096
  possible frame words**: the 4-slot frame lights exactly the requested LEDs,
  each lit LED appears in exactly 1 of 4 slots (25 % duty regardless of how
  many are lit), never more than one line is driven HIGH, and no unrequested
  LED is ever forward-biased. It also checks the blanking window (no line
  driven while sampling, exactly one pull-up, S2/S1 alternating), the
  debounce, the buzzer gate waveform, and the sleep pin state.
- **The simulation can fail.** Five mutations of `orbit.c` — two HIGH lines,
  frozen slot counter, blanking removed, sleep pull-ups removed, buzzer gate
  left high, auto-repeating debounce — were each killed by the intended
  assertion.

Still bench facts, not proven here: LED brightness and the real duty, the
4.7 kΩ vs internal pull-up divider (the SPEC's 0.09–0.19·VCC), buzzer loudness
and current, sleep current, and RC oscillator accuracy.

**One thing to watch on first play** (a prediction, not a measured fault): a
*held* button pulls its line toward GND through 4.7 kΩ while that line is
hi-Z, which can trickle ≈0.5 mA through an LED whose cathode sits on that line
— a faint glow at a position nobody asked for. The SPEC says this is
invisible; if it is visible on the bench, that is an Article II incident and
the fix is a design decision, not a firmware patch.

## Open readings

The SPEC's *Firmware* section is one paragraph, so some game details are not
specified. Taken conservatively, never improvised silently:

1. **Does a near miss end the round?** Not stated. Taken as: any non-exact
   catch ends the round; near miss and miss differ only in feedback, which is
   exactly what the SPEC asks them to differ in.
2. **Does an unpressed lap cost anything?** Not stated, so nothing is
   invented — the round waits. The global idle timeout still puts an
   abandoned board to sleep.
3. **Is a score displayed?** Not stated, so **no score readout was invented**.
   The achieved speed *is* the score and it is visible as the speed of the
   ring. (A ring-count readout would have been easy and is deliberately absent.)
4. **Table shape.** The SPEC says `{high, low, position}`; the brief says a
   12-entry `{anode, cathode}` indexed by position. `matrix.h` writes all
   three columns per row and indexes the array by position via designated
   initialisers, which satisfies both readings and makes row order irrelevant.
5. **One pull-up at a time, not two.** The SPEC says "the line under test to
   input-pullup" (singular) and quotes a 0.09–0.19·VCC pressed level. Reading
   both buttons in one window would enable two pull-ups, and the ring then
   provides a second path into the pressed node (e.g. PB2's pull-up through
   R11 and the position-11 LED into a held-low L3), lifting the pressed level
   toward ~0.25·VCC against a VIL max of 0.3·VCC. The conservative reading —
   one line at a time, buttons alternating slot by slot — keeps the SPEC's own
   divider arithmetic true and still samples each button at 500 Hz.
6. **Debounce style.** The SPEC mandates firmware debounce but not its shape.
   This is *leading edge*: the first low sample fires, then the button must
   read high for 30 ms to re-arm. An N-of-N filter would add its whole window
   to the player's reaction time, and at a 45 ms step interval a 16 ms filter
   is a third of a ring position.
