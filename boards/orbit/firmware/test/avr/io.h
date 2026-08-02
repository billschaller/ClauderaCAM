/* test/avr/io.h -- host mock of the ATtiny85 I/O space.
 * Registers become ordinary variables so the host test can watch every write
 * the firmware makes to the port pins.  Bit numbers are the real ones. */
#ifndef MOCK_AVR_IO_H
#define MOCK_AVR_IO_H
#include <stdint.h>

uint8_t DDRB, PORTB, PINB, SREG;
uint8_t TCCR0A, TCCR0B, OCR0A, TIFR, TIMSK;
uint8_t GIMSK, GIFR, PCMSK, CLKPR, PRR;
uint8_t ADMUX, ADCSRA, ADCH, DIDR0;

#define PB0 0
#define PB1 1
#define PB2 2
#define PB3 3
#define PB4 4
#define PB5 5

#define WGM01 1
#define CS00  0
#define CS01  1
#define OCF0A 4
#define OCIE0A 4
#define PCIE  5
#define PCIF  5
#define CLKPCE 7
#define PRTIM1 3
#define PRUSI  1
#define PRADC  0
#define ADLAR 5
#define ADEN  7
#define ADSC  6
#define ADPS2 2

#endif
