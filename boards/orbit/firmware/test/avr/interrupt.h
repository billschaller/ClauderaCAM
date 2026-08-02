/* test/avr/interrupt.h -- host mock.  An ISR becomes a plain function the
 * test can call once per simulated display slot. */
#ifndef MOCK_AVR_INTERRUPT_H
#define MOCK_AVR_INTERRUPT_H

#define TIMER0_COMPA_vect isr_timer0_compa
#define PCINT0_vect       isr_pcint0

#define ISR(vec)             void vec(void)
#define EMPTY_INTERRUPT(vec) void vec(void) { }

void mock_cli(void);
void mock_sei(void);
#define cli() mock_cli()
#define sei() mock_sei()

#endif
