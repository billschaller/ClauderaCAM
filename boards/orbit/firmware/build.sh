#!/bin/sh
# build.sh -- Board B "orbit" firmware.
#
# Bare-metal avr-gcc.  No Arduino core, no Arduino API, no sketch: arduino-cli
# is used ONLY as the package manager that puts a known-good AVR toolchain on
# disk, and the compiler is then invoked directly on pure C.
#
# Toolchain provenance (installed user-locally, no sudo):
#   arduino-cli 1.5.2-rc.1  ->  arduino:avr@1.8.8
#   avr-gcc     7.3.0-atmel3.6.1-arduino7
#   avrdude     8.0.0-arduino1
#
# If AVR_BIN below does not exist, recreate it with:
#   curl -sSL -o acli.tgz \
#     https://downloads.arduino.cc/arduino-cli/arduino-cli_latest_Linux_64bit.tar.gz
#   tar xzf acli.tgz -C ./bin arduino-cli
#   ./bin/arduino-cli core update-index
#   ./bin/arduino-cli core install arduino:avr

set -e
cd "$(dirname "$0")"

AVR_BIN="$HOME/.arduino15/packages/arduino/tools/avr-gcc/7.3.0-atmel3.6.1-arduino7/bin"
CC="$AVR_BIN/avr-gcc"
OBJCOPY="$AVR_BIN/avr-objcopy"
SIZE="$AVR_BIN/avr-size"

MCU=attiny85
F_CPU=8000000UL      # internal RC, CKDIV8 cleared (lfuse 0xE2)

# -Werror: the SPEC asks for a zero-warning build, so the build enforces it.
CFLAGS="-mmcu=$MCU -DF_CPU=$F_CPU -Os -std=gnu11 -Wall -Wextra -Werror \
        -funsigned-char -funsigned-bitfields -fpack-struct -fshort-enums \
        -ffunction-sections -fdata-sections"
LDFLAGS="-mmcu=$MCU -Wl,--gc-sections"

build() {                       # build <suffix> <extra-cflags>
	out="orbit$1"
	echo "=== $out"
	echo "\$ $CC $CFLAGS $2 -c orbit.c -o $out.o"
	$CC $CFLAGS $2 -c orbit.c -o "$out.o"
	echo "\$ $CC $LDFLAGS -o $out.elf $out.o -Wl,-Map,$out.map"
	$CC $LDFLAGS -o "$out.elf" "$out.o" -Wl,-Map,"$out.map"
	echo "\$ $OBJCOPY -O ihex -R .eeprom $out.elf $out.hex"
	$OBJCOPY -O ihex -R .eeprom "$out.elf" "$out.hex"
	$SIZE --mcu=$MCU -C "$out.elf"
	echo
}

# --- host-side matrix sanity (runs first: a bad table must fail cheaply) ---
echo "=== host matrix check"
cc -std=c11 -Wall -Wextra -Werror matrix_check.c -o matrix_check
./matrix_check
echo

# --- host-side logic simulation: compiles the REAL orbit.c against mocked
#     AVR headers and checks the drive pattern against an independent LED
#     model over all 4096 frame words. ---
echo "=== host logic simulation"
cc -std=c11 -I. -Itest -Wall -Wextra -Werror orbit_test.c -o orbit_test
./orbit_test
echo

# --- default: digital button read (SPEC Decision 3) ---
build ""     "-DORBIT_BUTTON_ADC=0"

# --- designed-in escalation: ADC threshold read, no hardware change.
#     Built every time so the fallback cannot bit-rot. ---
build "-adc" "-DORBIT_BUTTON_ADC=1"

echo "flash target: orbit.hex   (fallback build: orbit-adc.hex)"
