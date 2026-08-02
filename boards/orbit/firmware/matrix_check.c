/*
 * matrix_check.c -- host-side sanity harness for the ring matrix.
 *
 * It compiles the SAME matrix.h the firmware compiles, so the _Static_assert
 * chain in that header fires here too (a bad transcription of MATRIX.md fails
 * this build before it ever reaches avr-gcc).  On top of that it re-proves the
 * same properties at runtime, by exhaustive construction rather than by
 * bit-fold arithmetic -- two independent statements of the same truth, which
 * is the point of a cross-check.
 *
 *   cc -std=c11 -Wall -Wextra -Werror matrix_check.c -o matrix_check && ./matrix_check
 */

#include <stdio.h>
#include <string.h>
#include <stdlib.h>

#include "matrix.h"

static int fail;

static void check(int ok, const char *what)
{
	printf("  [%s] %s\n", ok ? "PASS" : "FAIL", what);
	if (!ok) fail = 1;
}

int main(void)
{
	unsigned seen[LINE_N][LINE_N];
	memset(seen, 0, sizeof seen);

	printf("orbit ring matrix (FINAL -- transcribed from MATRIX.md @ 34a0fbc)\n");
	printf("  pos  anode  cathode\n");
	for (int i = 0; i < RING_N; i++)
		printf("  %3d  L%u     L%u\n", i + 1,
		       (unsigned)ring_map[i].anode, (unsigned)ring_map[i].cathode);
	printf("\n");

	/* 1. every entry is a real, forward-biasable pair on real lines */
	int ok = 1;
	for (int i = 0; i < RING_N; i++) {
		unsigned a = ring_map[i].anode, c = ring_map[i].cathode;
		if (a >= LINE_N || c >= LINE_N || a == c) ok = 0;
		else seen[a][c]++;
	}
	check(ok, "all 12 entries name lines 0..3 with anode != cathode");

	/* 2. distinct: no ordered pair used twice */
	ok = 1;
	for (int a = 0; a < LINE_N; a++)
		for (int c = 0; c < LINE_N; c++)
			if (seen[a][c] > 1) ok = 0;
	check(ok, "all 12 (anode,cathode) pairs are distinct");

	/* 3. complete: the whole 4x3 ordered set is covered, so the boot
	 *    self-test walk energises every legal line combination */
	ok = 1;
	for (int a = 0; a < LINE_N; a++)
		for (int c = 0; c < LINE_N; c++)
			if (a != c && seen[a][c] != 1) ok = 0;
	check(ok, "the 4x3 = 12 ordered pairs are all covered exactly once");

	/* 4. no ring position was left as an implicit-zero hole in the
	 *    designated-initialiser array (an all-zero entry is a == c == 0,
	 *    which check 1 would already reject -- this states it directly) */
	ok = 1;
	for (int i = 0; i < RING_N; i++)
		if (ring_map[i].anode == 0 && ring_map[i].cathode == 0) ok = 0;
	check(ok, "no ring position is an undefined {0,0} hole");

	/* 5. the marker is position 1 = frame bit 0 */
	check(MARKER_BIT == 1u, "marker (ring position 1) is frame bit 0");

	printf("\n%s\n", fail ? "MATRIX CHECK FAILED" : "MATRIX CHECK PASSED");
	return fail ? EXIT_FAILURE : EXIT_SUCCESS;
}
