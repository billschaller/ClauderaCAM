/* test/avr/sleep.h -- host mock.  sleep_cpu() snapshots the pin state so the
 * test can prove the SPEC's sleep contract (all lines input-pullup, buzzer
 * gate driven low) without a chip. */
#ifndef MOCK_AVR_SLEEP_H
#define MOCK_AVR_SLEEP_H

#define SLEEP_MODE_PWR_DOWN 2
void mock_sleep_cpu(void);

#define set_sleep_mode(m) ((void)(m))
#define sleep_enable()    ((void)0)
#define sleep_disable()   ((void)0)
#define sleep_cpu()       mock_sleep_cpu()

#endif
