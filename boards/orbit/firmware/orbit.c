/*
 * orbit.c -- Board B "orbit" firmware.  ATtiny85, bare metal, no Arduino core.
 *
 * Every block below cites the SPEC.md constraint it serves.  The SPEC is the
 * contract; where it is silent this file takes the conservative reading and
 * says so in a comment (grep "AMBIGUITY").
 *
 * Hardware contract (SPEC "Pin budget", "The ring", "Buzzer cell"):
 *   PB0..PB3 = charlieplex lines L0..L3, 12 LEDs = all 4x3 ordered pairs.
 *   PB3 also carries S1 CATCH, PB2 also carries S2 START (4.7k to GND).
 *   PB4 gates Q2 which sinks an ACTIVE buzzer -- a gate, never a pitch.
 *   PB5 stays RESET (RSTDISBL is never programmed; ISP must survive).
 *   Internal 8 MHz RC, CKDIV8 cleared (lfuse 0xE2).  5 V nominal.
 *
 * Build: see build.sh / README.md.  -Os -Wall -Wextra, zero warnings.
 */

#include <avr/io.h>
#include <avr/interrupt.h>
#include <avr/pgmspace.h>
#include <avr/sleep.h>
#include <util/delay.h>
#include <stdint.h>

#include "matrix.h"

/* Button read strategy.  Digital is the default (SPEC Decision 3: "button
 * read -> blanking-window digital first"); -DORBIT_BUTTON_ADC=1 selects the
 * designed-in escalation.  Defined here so the file also builds standalone. */
#ifndef ORBIT_BUTTON_ADC
#define ORBIT_BUTTON_ADC 0
#endif

/* ------------------------------------------------------------------ *
 * 0.  Pin map and tunables                                            *
 * ------------------------------------------------------------------ */

#define LINE_MASK ((uint8_t)((1u << PB0) | (1u << PB1) | (1u << PB2) | (1u << PB3)))
#define SND_MASK  ((uint8_t)(1u << PB4))   /* buzzer gate -> R14 -> Q2 base */
#define S1_BIT    ((uint8_t)(1u << PB3))   /* CATCH, shares line L3 */
#define S2_BIT    ((uint8_t)(1u << PB2))   /* START, shares line L2 */

#define SLOT_HZ            1000u  /* Timer0 CTC tick = one display slot   */
#define FRAME_HZ           (SLOT_HZ / LINE_N)          /* = 250 Hz        */
#define BTN_SETTLE_US      10     /* SPEC: "settle ~10 us" before sampling */
#define BTN_SAMPLE_MS      2      /* each button sampled on alternate slots */
#define BTN_RELEASE_MS     30     /* release time before the button re-arms */

#define SELFTEST_STEP_MS   120u   /* per ring position during the boot walk */
#define ATTRACT_STEP_MS    150u
#define MARKER_BLINK_MS    350u   /* deliberately NOT a multiple of the above */
#define START_INTERVAL_MS  220u
#define MIN_INTERVAL_MS    45u
#define IDLE_SLEEP_MS      30000u

/* ms_ticks is 16-bit, so every elapsed-time test must span < 32768 ms. */
_Static_assert(IDLE_SLEEP_MS < 32768u, "idle timeout must fit the wrap-safe 16-bit tick compare");
_Static_assert(START_INTERVAL_MS > MIN_INTERVAL_MS, "ramp must have somewhere to go");

/* ------------------------------------------------------------------ *
 * 1.  Shared state                                                    *
 * ------------------------------------------------------------------ */

static volatile uint16_t frame;     /* bit N = ring position N+1 is lit */
static volatile uint16_t ms_ticks;  /* incremented once per display slot */
static uint8_t slot;                /* 0..3, ISR-private */

/* Atomic 16-bit access: the ISR writes ms_ticks and reads frame, so the main
 * loop must not be caught mid-word.  SREG save/restore keeps these callable
 * from anywhere without assuming interrupts were on. */
static uint16_t ms_now(void)
{
	uint8_t s = SREG; cli();
	uint16_t v = ms_ticks;
	SREG = s;
	return v;
}

static void frame_set(uint16_t f)
{
	uint8_t s = SREG; cli();
	frame = f;
	SREG = s;
}

static void wait_ms(uint16_t d)
{
	uint16_t t0 = ms_now();
	while ((uint16_t)(ms_now() - t0) < d) { }
}

/* ------------------------------------------------------------------ *
 * 2.  Buzzer -- PB4 GATES an active element (SPEC "Buzzer cell")      *
 * ------------------------------------------------------------------ *
 * BZ1 is a Cylewet CYT1036, 5 V ACTIVE magnetic buzzer with its own
 * oscillator (Decision 2).  There is no pitch control and none is faked:
 * a pattern is a list of durations in ms, alternating ON, OFF, ON, ...
 * Timer1/OC1B is left completely unused -- SPEC keeps it as headroom in
 * case BZ1 is ever swapped for a passive element. */

static const uint16_t BZ_CHIRP[] PROGMEM = { 40 };                  /* self-test done */
static const uint16_t BZ_START[] PROGMEM = { 60 };                  /* round begins    */
static const uint16_t BZ_WIN[]   PROGMEM = { 30, 50, 30, 50, 30 };  /* exact hit       */
static const uint16_t BZ_NEAR[]  PROGMEM = { 150 };                 /* near miss       */
static const uint16_t BZ_MISS[]  PROGMEM = { 450 };                 /* miss            */

static const uint16_t *volatile bz_seq;
static volatile uint8_t  bz_n, bz_i;
static volatile uint16_t bz_left;

#define BUZZ(p) buzz((p), (uint8_t)(sizeof(p) / sizeof((p)[0])))

static void buzz(const uint16_t *seq, uint8_t n)
{
	uint8_t s = SREG; cli();
	bz_seq = seq; bz_n = n; bz_i = 0;
	bz_left = pgm_read_word(&seq[0]);
	PORTB |= SND_MASK;              /* Q2 on: BZ1 sounds at its own pitch */
	SREG = s;
}

/* Called once per slot from the display ISR -- the SPEC says the beep
 * patterns run "off Timer0's tick", so there is exactly one time base. */
static void buzz_tick(void)
{
	if (!bz_n) return;
	if (--bz_left) return;
	if (++bz_i >= bz_n) { bz_n = 0; PORTB &= (uint8_t)~SND_MASK; return; }
	bz_left = pgm_read_word(&bz_seq[bz_i]);
	if (bz_i & 1) PORTB &= (uint8_t)~SND_MASK;   /* odd index = gap  */
	else          PORTB |=            SND_MASK;  /* even index = on  */
}

/* ------------------------------------------------------------------ *
 * 3.  Buttons -- read ONLY in the blanking window                     *
 * ------------------------------------------------------------------ *
 * SPEC "Pin budget": the buttons hang on charlieplex lines L3 and L2
 * through 4.7k to GND, because there is no sixth pin.  They can therefore
 * only be read when NO line is being driven -- otherwise the reading is
 * whatever the display is doing, and enabling a pull-up on a driven line
 * would fight the driver.  Hence: blank all four lines to input hi-Z,
 * pull up the ONE line under test, settle ~10 us, sample, restore.
 *
 * ONE line at a time, deliberately.  The SPEC's pressed-level band
 * (0.09-0.19*VCC, from 4.7k against the 20-50k internal pull-up) assumes a
 * single pull-up source.  With both PB2 and PB3 pulled up at once, the ring
 * itself provides a second path into the pressed node (e.g. PB2's pull-up
 * through R11 and the position-11 LED into a held-low L3), which lifts the
 * pressed level toward 0.25*VCC -- uncomfortably close to VIL max 0.3*VCC.
 * Alternating slots costs nothing: each button is still sampled at 500 Hz.
 *
 * There is NO hardware debounce by design (SPEC: "no debounce capacitor: a
 * cap on a matrix line smears every LED slot.  Debounce is firmware.").
 * The debounce here is LEADING EDGE: the first low sample fires the event,
 * then the button must read high continuously for BTN_RELEASE_MS to re-arm.
 * That is the right shape for a reflex game -- a trailing-edge or N-of-N
 * filter would add its whole window to the human's reaction time, and at a
 * 45 ms step interval a 16 ms filter is a third of a ring position. */

typedef struct {
	volatile uint8_t event; /* set by the ISR, consumed by the game loop */
	uint8_t armed;          /* ISR-private */
	uint8_t lock;           /* ISR-private: ms of release still required */
} btn_t;

static btn_t s_catch, s_start;

#if ORBIT_BUTTON_ADC
/* Designed-in escalation path (SPEC + Decision 3): "if the digital margin
 * disappoints on the bench, firmware escalates to an ADC threshold read with
 * NO hardware change".  Same pins, same pull-up, same blanking window -- only
 * the comparison changes.  PB3 = ADC3, PB2 = ADC1.  Default is digital. */
#define ADC_MUX_S1  3u    /* PB3 = ADC3 */
#define ADC_MUX_S2  1u    /* PB2 = ADC1 */
#define ADC_PRESS_THRESHOLD 128u  /* 8-bit, VCC ref: 0.5*VCC.  Pressed is
                                   * 0.09-0.19*VCC, released floats to the
                                   * pull-up rail -- nothing lands near half. */
static uint8_t line_pressed(uint8_t bit)
{
	ADMUX = (uint8_t)((1u << ADLAR) | ((bit == S1_BIT) ? ADC_MUX_S1 : ADC_MUX_S2));
	PORTB |= bit;
	_delay_us(BTN_SETTLE_US);
	ADCSRA |= (uint8_t)(1u << ADSC);
	while (ADCSRA & (uint8_t)(1u << ADSC)) { }
	uint8_t v = ADCH;               /* 8 bits is all a threshold needs */
	PORTB &= (uint8_t)~bit;
	return (uint8_t)(v < ADC_PRESS_THRESHOLD);
}
#else
static uint8_t line_pressed(uint8_t bit)
{
	PORTB |= bit;                   /* input + pull-up (DDR is already 0) */
	_delay_us(BTN_SETTLE_US);
	uint8_t low = (uint8_t)((PINB & bit) == 0);   /* pressed = LOW */
	PORTB &= (uint8_t)~bit;         /* back to hi-Z before the next slot */
	return low;
}
#endif

static void btn_sample(btn_t *b, uint8_t pressed)
{
	if (pressed) {
		if (b->armed) { b->armed = 0; b->event = 1; }
		b->lock = BTN_RELEASE_MS;   /* restart the release timer while held */
	} else if (!b->armed) {
		if (b->lock > BTN_SAMPLE_MS) b->lock = (uint8_t)(b->lock - BTN_SAMPLE_MS);
		else { b->lock = 0; b->armed = 1; }
	}
}

static uint8_t btn_take(btn_t *b)
{
	uint8_t s = SREG; cli();
	uint8_t v = b->event; b->event = 0;
	SREG = s;
	return v;
}

/* ------------------------------------------------------------------ *
 * 4.  Display ISR -- 4-slot row scan, 1 kHz slots, 250 Hz frame       *
 * ------------------------------------------------------------------ *
 * SPEC "The ring": "Drive one line HIGH, up to three lines LOW, the rest
 * hi-Z; 4 slots per frame at ~250 Hz frame rate (1 kHz slot rate, Timer0
 * CTC).  Any lit LED gets 25% duty regardless of how many are lit."
 *
 * Exactly ONE line is ever HIGH.  Two HIGH lines would forward-bias the LEDs
 * of two rows into the same cathodes, lighting positions nobody asked for and
 * doubling the current out of a single pin past the 20 mA budget the 560 R
 * series resistors were sized against (SPEC "Power budget": 3 LEDs = 15.5 mA
 * plus 1.1 mA of held-button current = 16.6 mA, margin intact).
 *
 * Order inside the ISR: blank, sample, then light.  The blank+sample window
 * is ~15 us of a 1000 us slot, so a lit LED keeps ~98.5% of its 25% duty. */

ISR(TIMER0_COMPA_vect)
{
	/* (a) blanking window: all four lines input, no pull-up. */
	DDRB  &= (uint8_t)~LINE_MASK;
	PORTB &= (uint8_t)~LINE_MASK;

	/* (b) one button per slot, while nothing is driven. */
	if (slot & 1) btn_sample(&s_catch, line_pressed(S1_BIT));
	else          btn_sample(&s_start, line_pressed(S2_BIT));

	/* (c) advance to the next row. */
	slot = (uint8_t)((slot + 1u) & 3u);

	/* (d) collect the cathodes of every lit LED whose anode is this row.
	 *     The matrix table is the ONLY thing a layout permutation changes. */
	uint16_t f = frame;
	uint8_t lows = 0;
	for (uint8_t i = 0; i < RING_N; i++) {
		if (f & (uint16_t)(1u << i)) {
			if (pgm_read_byte(&ring_map[i].anode) == slot)
				lows |= (uint8_t)(1u << pgm_read_byte(&ring_map[i].cathode));
		}
	}

	/* (e) drive: anode HIGH, those cathodes LOW, everything else hi-Z.
	 *     PB4 is preserved by the read-modify-write -- the buzzer gate must
	 *     not blink at 250 Hz.  `lows` can never contain the anode bit: the
	 *     anode != cathode static assert in matrix.h guarantees it. */
	if (lows) {
		PORTB = (uint8_t)((PORTB & (uint8_t)~LINE_MASK) | (uint8_t)(1u << slot));
		DDRB  = (uint8_t)((DDRB  & (uint8_t)~LINE_MASK) | (uint8_t)(1u << slot) | lows);
	}

	ms_ticks++;
	buzz_tick();
}

/* Wake source only -- the handler itself has nothing to do (SPEC: the matrix
 * lines carry PCINT, "so sleep/wake needs no extra part"). */
EMPTY_INTERRUPT(PCINT0_vect)

/* ------------------------------------------------------------------ *
 * 5.  Init and sleep                                                  *
 * ------------------------------------------------------------------ */

static void display_start(void)
{
	DDRB  &= (uint8_t)~LINE_MASK;   /* all lines hi-Z until the ISR drives */
	PORTB &= (uint8_t)~LINE_MASK;
	DDRB  |= SND_MASK;              /* buzzer gate is always an output... */
	PORTB &= (uint8_t)~SND_MASK;    /* ...and starts LOW: Q2 off */

	TCCR0A = (uint8_t)(1u << WGM01);                       /* CTC          */
	TCCR0B = (uint8_t)((1u << CS01) | (1u << CS00));       /* clk/64       */
	OCR0A  = (uint8_t)((F_CPU / 64u / SLOT_HZ) - 1u);      /* = 124 -> 1 kHz */
	TIFR  |= (uint8_t)(1u << OCF0A);
	TIMSK |= (uint8_t)(1u << OCIE0A);
}

static void hw_init(void)
{
	/* SPEC/README require lfuse 0xE2 (internal 8 MHz RC, CKDIV8 CLEARED).
	 * This timed sequence forces the runtime prescaler to /1 as well, so a
	 * chip flashed BEFORE its fuses were written still keeps the advertised
	 * 1 kHz slot rate.  It is a belt, not a replacement for the fuse: see
	 * README "Fuses" -- the fuse is still mandatory, because CKDIV8 also
	 * governs the clock during ISP itself. */
	CLKPR = (uint8_t)(1u << CLKPCE);
	CLKPR = 0;

#if ORBIT_BUTTON_ADC
	PRR = (uint8_t)((1u << PRTIM1) | (1u << PRUSI));
	/* /16 -> 500 kHz ADC clock.  Above the 200 kHz full-10-bit window on
	 * purpose: this read is an 8-bit threshold, and a 26 us conversion fits
	 * the blanking window where a 104 us one would eat 10% of the slot. */
	ADCSRA = (uint8_t)((1u << ADEN) | (1u << ADPS2));
	/* DIDR0 is deliberately NOT touched: disabling the digital input buffer
	 * on PB2/PB3 would also kill the PCINT wake path. */
#else
	PRR = (uint8_t)((1u << PRTIM1) | (1u << PRUSI) | (1u << PRADC));
#endif

	slot = 0;
	frame = 0;
	s_catch.armed = 1;
	s_start.armed = 1;
	display_start();
}

/* SPEC: "after an idle timeout, power-down sleep with pin-change wake on
 * PB2/PB3, all lines input-pullup, buzzer off".  Power-down stops the I/O
 * clock, so Timer0 cannot wake us -- PCINT is the only way out, which is why
 * the pull-ups must be on before we sleep (a floating line neither holds a
 * level nor generates a clean change). */
static void orbit_sleep(void)
{
	TIMSK &= (uint8_t)~(1u << OCIE0A);
	frame = 0;

	PORTB &= (uint8_t)~SND_MASK;    /* buzzer gate LOW, and kept an OUTPUT: */
	DDRB  |= SND_MASK;              /* a pull-up here would bias Q2 on.     */
	bz_n = 0;

	DDRB  &= (uint8_t)~LINE_MASK;   /* all four lines input... */
	PORTB |= LINE_MASK;             /* ...with pull-ups: no LED sees a bias */

#if ORBIT_BUTTON_ADC
	ADCSRA &= (uint8_t)~(1u << ADEN);
#endif

	PCMSK = (uint8_t)(S1_BIT | S2_BIT);   /* PCINT3 (S1) + PCINT2 (S2) */
	GIFR |= (uint8_t)(1u << PCIF);
	GIMSK |= (uint8_t)(1u << PCIE);

	set_sleep_mode(SLEEP_MODE_PWR_DOWN);
	cli();
	sleep_enable();
	sei();
	sleep_cpu();
	sleep_disable();

	GIMSK &= (uint8_t)~(1u << PCIE);
	PCMSK = 0;

#if ORBIT_BUTTON_ADC
	ADCSRA |= (uint8_t)(1u << ADEN);
#endif

	/* Wake resumes ATTRACT, not a round: the press that woke the board is
	 * consumed here so it cannot also start a game the operator did not ask
	 * for.  Both buttons re-arm only after a real release (armed = 0). */
	s_catch.armed = 0; s_catch.lock = BTN_RELEASE_MS; s_catch.event = 0;
	s_start.armed = 0; s_start.lock = BTN_RELEASE_MS; s_start.event = 0;
	display_start();
}

/* ------------------------------------------------------------------ *
 * 6.  Ring helpers                                                    *
 * ------------------------------------------------------------------ */

#define ALL_BITS ((uint16_t)((1u << RING_N) - 1u))

static uint8_t ring_next(uint8_t i) { return (uint8_t)((i + 1u) % RING_N); }
static uint16_t ring_bit(uint8_t i) { return (uint16_t)(1u << i); }

/* Distance from ring index i to the marker (index 0), the short way round. */
static uint8_t ring_dist_to_marker(uint8_t i)
{
	return (uint8_t)((i <= RING_N / 2) ? i : (RING_N - i));
}

static void blink(uint16_t bits, uint8_t times, uint16_t on_ms, uint16_t off_ms)
{
	for (uint8_t k = 0; k < times; k++) {
		frame_set(bits);  wait_ms(on_ms);
		frame_set(0);     wait_ms(off_ms);
	}
}

/* ------------------------------------------------------------------ *
 * 7.  Boot self-test -- ALWAYS first (SPEC assembly step 13)          *
 * ------------------------------------------------------------------ *
 * "The boot self-test walks all 12 ring positions in order, then chirps.
 *  That walk is an electrical continuity test of every matrix pair, every
 *  series resistor and every via on the board -- if a position stays dark,
 *  the fault is in one of three named places."
 * One LED at a time is the point: a dark position accuses exactly one LED,
 * one resistor and the two wire vias that feed its corridor. */
static void self_test(void)
{
	for (uint8_t p = 0; p < RING_N; p++) {
		frame_set(ring_bit(p));
		wait_ms(SELFTEST_STEP_MS);
	}
	frame_set(0);
	BUZZ(BZ_CHIRP);
	wait_ms(300);
}

/* ------------------------------------------------------------------ *
 * 8.  Attract -- runs until START, or until the idle timeout sleeps   *
 * ------------------------------------------------------------------ *
 * SPEC: "The marker is silk, not a part.  Position 1 carries a lasered arrow
 * on F.Silkscreen and firmware blinks it differently."  A slow orbit gives
 * the blink something to be different FROM: the dot steps at 150 ms, the
 * marker blinks at 350 ms, so the marker is never mistaken for the dot. */
static void attract(void)
{
	uint8_t dot = 0, mark = 0;
	uint16_t t_dot = ms_now(), t_mark = t_dot, t_idle = t_dot;

	for (;;) {
		uint16_t now = ms_now();

		if ((uint16_t)(now - t_dot) >= ATTRACT_STEP_MS) {
			t_dot = now; dot = ring_next(dot);
		}
		if ((uint16_t)(now - t_mark) >= MARKER_BLINK_MS) {
			t_mark = now; mark ^= 1u;
		}
		frame_set((uint16_t)(ring_bit(dot) | (mark ? MARKER_BIT : 0u)));

		if (btn_take(&s_start)) return;          /* -> a round begins */
		if (btn_take(&s_catch)) t_idle = now;    /* activity, not a move */

		if ((uint16_t)(now - t_idle) >= IDLE_SLEEP_MS) {
			orbit_sleep();
			/* the tick stopped while asleep: rebase every timer */
			t_idle = t_dot = t_mark = ms_now();
		}
	}
}

/* ------------------------------------------------------------------ *
 * 9.  A round                                                         *
 * ------------------------------------------------------------------ *
 * SPEC: "the game loop ramps the chase interval, scores the catch against
 * position 1".  Exact hit -> win feedback and the ramp continues; near miss
 * and miss each get their own feedback and end the round.
 *
 * AMBIGUITY (noted, conservative reading): the SPEC does not say whether a
 * near miss ends the round, nor whether letting the dot lap unpressed costs
 * anything, nor that a score is ever displayed.  Taken conservatively:
 *   - any non-exact catch ends the round; near/miss differ only in their
 *     feedback, which is exactly what the SPEC asks them to differ in;
 *   - an unpressed lap costs nothing (nothing in the SPEC creates a penalty),
 *     and the global idle timeout still puts an abandoned board to sleep;
 *   - no score readout is invented.  The achieved speed IS the score, and it
 *     is visible as the speed of the ring.
 * The dot starts opposite the marker (position 7) so the first lap is a full
 * lap of reaction time rather than an instant free hit. */
static void play(void)
{
	uint16_t interval = START_INTERVAL_MS;
	uint8_t dot = RING_N / 2;               /* index 6 = ring position 7 */
	uint16_t t_dot = ms_now(), t_idle = t_dot;

	BUZZ(BZ_START);
	frame_set(ring_bit(dot));
	(void)btn_take(&s_catch);               /* drop anything pending */

	for (;;) {
		uint16_t now = ms_now();

		if ((uint16_t)(now - t_dot) >= interval) {
			t_dot = now;
			dot = ring_next(dot);
			frame_set(ring_bit(dot));
		}

		if (btn_take(&s_catch)) {
			uint8_t d = ring_dist_to_marker(dot);
			if (d == 0) {                                   /* exact hit */
				BUZZ(BZ_WIN);
				blink(MARKER_BIT, 3, 60, 60);
				interval = (uint16_t)(interval - interval / 5u); /* x0.8 */
				if (interval < MIN_INTERVAL_MS) interval = MIN_INTERVAL_MS;
				/* The feedback blink is a dead zone: a press latched while
				 * the marker was flashing must NOT be scored.  Without this
				 * drop it would fire on the next pass with the dot still
				 * parked on position 1 -- i.e. mashing S1 would farm free
				 * hits and ramp the speed without ever timing anything. */
				(void)btn_take(&s_catch);
				frame_set(ring_bit(dot));
				t_dot = ms_now();
				t_idle = t_dot;
				continue;
			}
			if (d == 1) {                                   /* near miss */
				BUZZ(BZ_NEAR);
				blink(ring_bit(dot), 2, 150, 150);
			} else {                                        /* miss      */
				BUZZ(BZ_MISS);
				blink(ALL_BITS, 3, 150, 150);
			}
			frame_set(0);
			return;
		}

		if (btn_take(&s_start)) t_idle = now;   /* START is inert mid-round */

		if ((uint16_t)(now - t_idle) >= IDLE_SLEEP_MS) {
			orbit_sleep();
			return;                              /* wake resumes attract */
		}
	}
}

/* ------------------------------------------------------------------ */

int main(void)
{
	hw_init();
	sei();
	self_test();            /* first, always -- it is the board's continuity test */
	for (;;) {
		attract();
		play();
	}
}
