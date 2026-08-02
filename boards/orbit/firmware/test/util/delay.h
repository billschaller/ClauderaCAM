/* test/util/delay.h -- host mock.  _delay_us is the settle window, which
 * makes it the perfect probe point: the mock samples the bus exactly where
 * the real chip would be waiting for the pull-up to win against 4.7k. */
#ifndef MOCK_UTIL_DELAY_H
#define MOCK_UTIL_DELAY_H
void mock_delay_us(double us);
#define _delay_us(x) mock_delay_us((double)(x))
#endif
