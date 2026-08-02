/*
 * orbit_test.c -- host-side simulation of the parts of orbit.c that cannot be
 * eyeballed: the charlieplex drive pattern, the blanking-window button read,
 * the debounce state machine, the buzzer sequencer and the sleep pin state.
 *
 * It compiles the REAL orbit.c against the mocked AVR headers under test/, so
 * the code under test is the code that gets flashed -- not a paraphrase.
 * The LED model used for the assertions is written independently of the
 * firmware's render loop: an LED is lit iff its anode line is driven HIGH and
 * its cathode line is driven LOW.  If the render loop and that model agree on
 * all 4096 possible frame words, the drive pattern is right.
 *
 * This does NOT prove anything about real silicon timing, LED brightness, or
 * the 4.7k/pull-up divider -- those are bench facts.  It proves the logic.
 *
 *   cc -std=c11 -I. -Itest -Wall -Wextra -Werror orbit_test.c -o orbit_test
 */

#include <stdio.h>
#include <string.h>
#include <stdlib.h>

#define F_CPU 8000000UL

/* Pull in the firmware itself; its main() is renamed out of the way. */
#define main orbit_main_unused
#include "orbit.c"
#undef main

/* ---------------- mock plumbing ---------------- */

static int   fails;
static int   settle_calls;
static uint8_t settle_ddrb, settle_portb, settle_pullup_bit;
static uint8_t mock_s1_pressed, mock_s2_pressed;
static int   slept;
static uint8_t sleep_ddrb, sleep_portb, sleep_pcmsk, sleep_gimsk, sleep_timsk;

void mock_cli(void) { SREG = (uint8_t)(SREG & 0x7Fu); }
void mock_sei(void) { SREG = (uint8_t)(SREG | 0x80u); }

/* The settle window is where the firmware waits for the internal pull-up to
 * beat the 4.7k series resistor to ground.  The mock resolves PINB there. */
void mock_delay_us(double us)
{
	(void)us;
	settle_calls++;
	settle_ddrb  = DDRB;
	settle_portb = PORTB;
	settle_pullup_bit = (uint8_t)(PORTB & LINE_MASK);

	PINB = 0xFF;
	if (mock_s1_pressed && (PORTB & S1_BIT) && !(DDRB & S1_BIT)) PINB = (uint8_t)(PINB & ~S1_BIT);
	if (mock_s2_pressed && (PORTB & S2_BIT) && !(DDRB & S2_BIT)) PINB = (uint8_t)(PINB & ~S2_BIT);
}

void mock_sleep_cpu(void)
{
	slept++;
	sleep_ddrb = DDRB; sleep_portb = PORTB;
	sleep_pcmsk = PCMSK; sleep_gimsk = GIMSK; sleep_timsk = TIMSK;
}

/* ---------------- assertions ---------------- */

static void ok(int cond, const char *what)
{
	if (!cond) { printf("  [FAIL] %s\n", what); fails++; }
	else       { printf("  [PASS] %s\n", what); }
}

static int popcount8(uint8_t v) { int n = 0; while (v) { n += v & 1u; v >>= 1; } return n; }

/* Independent model: which LEDs does the CURRENT pin state physically light? */
static uint16_t lit_now(void)
{
	uint16_t s = 0;
	for (int i = 0; i < RING_N; i++) {
		uint8_t a = ring_map[i].anode, c = ring_map[i].cathode;
		int a_high = ((DDRB >> a) & 1) && ((PORTB >> a) & 1);
		int c_low  = ((DDRB >> c) & 1) && !((PORTB >> c) & 1);
		if (a_high && c_low) s |= (uint16_t)(1u << i);
	}
	return s;
}

/* ---------------- tests ---------------- */

/* SPEC "The ring": one line HIGH, up to three LOW, rest hi-Z; 4 slots per
 * frame; any lit LED gets 25% duty regardless of how many are lit. */
static void test_display_exhaustive(void)
{
	int bad_union = 0, bad_duty = 0, bad_highs = 0, bad_ghost = 0;

	for (uint16_t want = 0; want < 4096u; want++) {
		frame_set(want);
		uint16_t seen_union = 0;
		uint8_t  seen_count[RING_N];
		memset(seen_count, 0, sizeof seen_count);

		for (int s = 0; s < LINE_N; s++) {
			isr_timer0_compa();
			uint8_t highs = (uint8_t)(DDRB & PORTB & LINE_MASK);
			if (popcount8(highs) > 1) bad_highs++;   /* never two anodes */
			uint16_t lit = lit_now();
			if (lit & (uint16_t)~want) bad_ghost++;  /* never an unasked LED */
			seen_union |= lit;
			for (int i = 0; i < RING_N; i++)
				if (lit & (uint16_t)(1u << i)) seen_count[i]++;
		}
		if (seen_union != want) bad_union++;
		for (int i = 0; i < RING_N; i++) {
			uint8_t wanted = (want >> i) & 1u;
			if (seen_count[i] != wanted) bad_duty++;  /* exactly 1 of 4 slots */
		}
	}
	frame_set(0);

	ok(!bad_union, "all 4096 frame words: the 4-slot frame lights exactly the requested LEDs");
	ok(!bad_duty,  "every lit LED appears in exactly 1 of the 4 slots (25% duty, independent of count)");
	ok(!bad_highs, "never more than one line driven HIGH in a slot");
	ok(!bad_ghost, "no unrequested LED is ever forward-biased");
}

/* SPEC "Pin budget": buttons are read in the blanking window, all four lines
 * to input, the line under test to input-pullup. */
static void test_blanking_window(void)
{
	int bad_ddr = 0, bad_pullups = 0;
	uint8_t order[8];

	frame_set(0x0FFF);                 /* worst case: every LED asking to be lit */
	for (int i = 0; i < 8; i++) {
		isr_timer0_compa();
		if (settle_ddrb & LINE_MASK) bad_ddr++;             /* all lines input */
		if (popcount8(settle_pullup_bit) != 1) bad_pullups++; /* exactly one pull-up */
		order[i] = settle_pullup_bit;
	}
	frame_set(0);

	ok(!bad_ddr, "during the settle window no charlieplex line is driven (all input hi-Z)");
	ok(!bad_pullups, "exactly one pull-up is on while sampling (the SPEC's 0.09-0.19*VCC divider assumes one)");

	int alt = 1;
	for (int i = 0; i < 8; i++) {
		uint8_t want = (i & 1) ? S1_BIT : S2_BIT;
		if (order[i] != want) alt = 0;
	}
	ok(alt, "S2/S1 alternate slot by slot (each button sampled at 500 Hz)");
}

/* SPEC: "no hardware debounce exists by design ... Debounce is firmware." */
static void test_debounce(void)
{
	btn_t b; memset(&b, 0, sizeof b); b.armed = 1;
	int events = 0;

	/* a bouncing press: first low sample must fire immediately (leading edge) */
	static const uint8_t bounce[] = { 1, 0, 1, 1, 0, 1, 1, 1 };
	for (unsigned i = 0; i < sizeof bounce; i++) {
		btn_sample(&b, bounce[i]);
		if (b.event) { events++; b.event = 0; }
	}
	ok(events == 1, "a bouncing press produces exactly one event, on the first low sample");

	/* still held: no repeats */
	for (int i = 0; i < 100; i++) { btn_sample(&b, 1); if (b.event) { events++; b.event = 0; } }
	ok(events == 1, "holding the button does not auto-repeat");

	/* release must last BTN_RELEASE_MS before the button re-arms */
	int samples_to_rearm = 0;
	while (!b.armed && samples_to_rearm < 100) { btn_sample(&b, 0); samples_to_rearm++; }
	ok(b.armed, "the button re-arms after a clean release");
	ok(samples_to_rearm * BTN_SAMPLE_MS >= BTN_RELEASE_MS,
	   "re-arming needs the full release window (>= 30 ms of continuous high)");

	btn_sample(&b, 1);
	ok(b.event == 1, "the next press after a clean release fires again");
}

/* SPEC "Buzzer cell": PB4 GATES an active element -- the pattern is a list of
 * on/off durations, and nothing ever tries to set a pitch. */
static void test_buzzer(void)
{
	uint8_t prev;
	int transitions[8], n = 0;

	frame_set(0);
	bz_n = 0; PORTB = (uint8_t)(PORTB & ~SND_MASK);
	BUZZ(BZ_WIN);
	ok((PORTB & SND_MASK) != 0, "buzzer gate goes high the moment a pattern starts");
	prev = (uint8_t)(PORTB & SND_MASK);

	for (int ms = 1; ms <= 400; ms++) {
		isr_timer0_compa();
		uint8_t now = (uint8_t)(PORTB & SND_MASK);
		if (now != prev && n < 8) transitions[n++] = ms;
		prev = now;
	}
	/* BZ_WIN = {30,50,30,50,30}: on 30, off 50, on 30, off 50, on 30, done. */
	int want[5] = { 30, 80, 110, 160, 190 };
	int match = (n == 5);
	for (int i = 0; i < 5 && i < n; i++) if (transitions[i] != want[i]) match = 0;
	ok(match, "BZ_WIN gates PB4 at exactly 30/50/30/50/30 ms then stops");
	ok((PORTB & SND_MASK) == 0, "the gate is left LOW when the pattern ends (Q2 off)");
}

/* SPEC: "after an idle timeout, power-down sleep with pin-change wake on
 * PB2/PB3, all lines input-pullup, buzzer off." */
static void test_sleep_state(void)
{
	slept = 0;
	orbit_sleep();
	ok(slept == 1, "orbit_sleep() actually reaches sleep_cpu()");
	ok((sleep_ddrb & LINE_MASK) == 0, "asleep: all four charlieplex lines are inputs");
	ok((sleep_portb & LINE_MASK) == LINE_MASK, "asleep: all four lines have pull-ups on");
	ok((sleep_ddrb & SND_MASK) && !(sleep_portb & SND_MASK),
	   "asleep: buzzer gate is an OUTPUT held LOW (a pull-up here would bias Q2 on)");
	ok(sleep_pcmsk == (uint8_t)(S1_BIT | S2_BIT), "asleep: PCINT armed on exactly PB2 and PB3");
	ok((sleep_gimsk & (1u << PCIE)) != 0, "asleep: pin-change interrupt enabled");
	ok((sleep_timsk & (1u << OCIE0A)) == 0, "asleep: the display tick is stopped");
	ok(s_catch.event == 0 && s_start.event == 0 && !s_catch.armed && !s_start.armed,
	   "on wake the waking press is consumed and both buttons need a real release");
}

/* SPEC: "scores the catch against position 1". */
static void test_ring_geometry(void)
{
	int d_ok = 1;
	uint8_t want[RING_N] = { 0,1,2,3,4,5,6,5,4,3,2,1 };
	for (int i = 0; i < RING_N; i++) if (ring_dist_to_marker((uint8_t)i) != want[i]) d_ok = 0;
	ok(d_ok, "ring distance to the marker is the short way round (0,1..6..1)");

	int n_ok = 1;
	for (int i = 0; i < RING_N; i++) if (ring_next((uint8_t)i) != (i + 1) % RING_N) n_ok = 0;
	ok(n_ok, "the dot advances clockwise and wraps 12 -> 1");

	ok(MARKER_BIT == 1u && ring_dist_to_marker(0) == 0,
	   "an exact hit is ring position 1, which is frame bit 0");
	ok(ring_dist_to_marker(1) == 1 && ring_dist_to_marker(RING_N - 1) == 1,
	   "a near miss is either neighbour of the marker (positions 2 and 12)");
}

int main(void)
{
	hw_init();
	SREG = 0x80;

	printf("orbit firmware -- host logic simulation\n\n");
	printf("display (charlieplex drive):\n");       test_display_exhaustive();
	printf("blanking-window button read:\n");       test_blanking_window();
	printf("firmware debounce:\n");                 test_debounce();
	printf("buzzer gate sequencing:\n");            test_buzzer();
	printf("sleep pin state:\n");                   test_sleep_state();
	printf("ring geometry / scoring:\n");           test_ring_geometry();

	printf("\nsettle-window samples taken: %d\n", settle_calls);
	printf("%s\n", fails ? "ORBIT LOGIC TESTS FAILED" : "ORBIT LOGIC TESTS PASSED");
	return fails ? EXIT_FAILURE : EXIT_SUCCESS;
}
