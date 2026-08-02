/*
 * matrix.h -- Board B "orbit": ring position -> charlieplex (anode, cathode)
 *             pair map, plus the compile-time proof that the map is a legal
 *             4-line charlieplex set.
 *
 * >>> FINAL <<<
 * Transcribed 2026-08-02 from boards/orbit/MATRIX.md at commit 34a0fbc
 * (the routed 66x56 board): the layout exercised its sanctioned freedom
 * and the ring->pair assignment is a full permutation of the SPEC
 * default (position 1, the marker, is LED8 on L2->L1). The SPEC states:
 *
 *     "The mapping is a LAYOUT degree of freedom.  Firmware holds a 12-entry
 *      {high, low} table; the layout may permute the ring->pair assignment
 *      freely to cut crossings, and the firmware table follows.  The table
 *      above is the default the layout starts from, not a constraint it must
 *      fight."
 *
 * and Decision 5 ("Vias -> trial then permute") makes a permutation the
 * expected outcome if the trial route wants more than 6 vias.  When
 * boards/orbit/MATRIX.md lands, transcribe its 12 rows into ORBIT_RING_MAP
 * below and rebuild.  NOTHING ELSE IN THE FIRMWARE CHANGES -- see README.md,
 * "Transcribing MATRIX.md".
 *
 * Lines are numbered by their port bit, which is also the SPEC's line number:
 *   L0 = PB0, L1 = PB1, L2 = PB2, L3 = PB3   (SPEC "Pin budget")
 *
 * The rows are written as (ring position, anode line, cathode line) -- the
 * SPEC's {high, low, position} triple.  The array is built with DESIGNATED
 * initialisers indexed by position, so the ROW ORDER IN THIS FILE IS
 * IRRELEVANT: position 7's row defines position 7 wherever it is written.
 * Transcription cannot silently shift the ring by mis-ordering rows.
 */

#ifndef ORBIT_MATRIX_H
#define ORBIT_MATRIX_H

#include <stdint.h>

#ifdef __AVR__
#include <avr/pgmspace.h>
#else
#define PROGMEM /* host sanity harness: matrix_check.c compiles this same file */
#endif

#define RING_N 12
#define LINE_N 4

/* ------------------------------------------------------------------ *
 *  X( ring position 1..12 , anode line (driven HIGH) , cathode line ) *
 * ------------------------------------------------------------------ *
 * Position 1 is the marker (12 o'clock, silk arrow), running clockwise.
 * Sectors of two adjacent positions share one line pair -- that pairing is
 * the SPEC's via-saving move (1,2 = L0/L1; 3,4 = L0/L2; 5,6 = L0/L3;
 * 7,8 = L1/L2; 9,10 = L1/L3; 11,12 = L2/L3).  A permutation from MATRIX.md
 * may break that grouping; the firmware does not care, only the router does.
 */
#define ORBIT_RING_MAP(X)   \
	X( 1, 2, 1)  /* marker */ \
	X( 2, 1, 2)               \
	X( 3, 3, 2)               \
	X( 4, 2, 3)               \
	X( 5, 1, 3)               \
	X( 6, 3, 1)               \
	X( 7, 1, 0)               \
	X( 8, 0, 1)               \
	X( 9, 3, 0)               \
	X(10, 0, 3)               \
	X(11, 0, 2)               \
	X(12, 2, 0)

typedef struct {
	uint8_t anode;   /* line driven HIGH in this LED's slot */
	uint8_t cathode; /* line driven LOW  in this LED's slot */
} led_t;

/* Indexed by (ring position - 1). */
static const led_t ring_map[RING_N] PROGMEM = {
#define X(pos, a, c) [(pos) - 1] = { (a), (c) },
	ORBIT_RING_MAP(X)
#undef X
};

/* ------------------------------------------------------------------ *
 *  Compile-time proof that the table is a legal charlieplex map.      *
 *  These asserts are the reason a bad transcription of MATRIX.md is a *
 *  BUILD FAILURE and not a dark LED discovered on the bench.          *
 * ------------------------------------------------------------------ */

/* (a) every entry names real lines and is a real LED: a line cannot be both
 *     the anode and the cathode of the same diode. */
#define X(pos, a, c) \
	_Static_assert((a) < LINE_N && (c) < LINE_N && (a) != (c), \
	               "ring position " #pos ": anode/cathode must be 0..3 and differ");
ORBIT_RING_MAP(X)
#undef X

/* (b) exactly 12 rows. */
#define X(pos, a, c) +1
_Static_assert((0 ORBIT_RING_MAP(X)) == RING_N,
               "ORBIT_RING_MAP must have exactly 12 rows");
#undef X

/* (c) the 12 rows are positions 1..12, each exactly once.  Twelve rows whose
 *     position bits OR to twelve distinct bits must, by pigeonhole, be a
 *     permutation of 1..12 -- so no position is defined twice or left as an
 *     implicit-zero hole in the designated-initialiser array. */
#define X(pos, a, c) | (1UL << (pos))
_Static_assert((0UL ORBIT_RING_MAP(X)) == 0x00001FFEUL,
               "ORBIT_RING_MAP must cover ring positions 1..12 exactly once");
#undef X

/* (d) the 12 (anode,cathode) pairs are DISTINCT and cover the whole 4x3
 *     ordered set.  Encode each pair as bit (anode*4 + cathode): the 16
 *     codes minus the four illegal diagonal codes (0,5,10,15) are exactly
 *     0x7BDE.  Twelve rows ORing to twelve distinct bits are all distinct
 *     (pigeonhole again), so this single assert catches a duplicated pair,
 *     a missing pair, and a swapped-polarity typo at once.
 *     This is what makes the boot self-test a COMPLETE continuity test:
 *     if all 12 pairs are distinct and cover the set, walking the ring
 *     energises every one of the four lines' twelve ordered combinations. */
#define X(pos, a, c) | (1UL << ((a) * 4 + (c)))
_Static_assert((0UL ORBIT_RING_MAP(X)) == 0x00007BDEUL,
               "ORBIT_RING_MAP pairs must be distinct and cover all 4x3 ordered pairs");
#undef X

/* (e) the marker is ring position 1 (SPEC: silk arrow at 12 o'clock, and the
 *     firmware blinks it).  Bit 0 of the frame word is therefore the marker. */
#define MARKER_POS 1
#define MARKER_BIT ((uint16_t)1u << (MARKER_POS - 1))
_Static_assert(MARKER_POS >= 1 && MARKER_POS <= RING_N, "marker must be a ring position");

#endif /* ORBIT_MATRIX_H */
