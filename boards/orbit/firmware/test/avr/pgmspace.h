/* test/avr/pgmspace.h -- host mock: flash is just memory here. */
#ifndef MOCK_AVR_PGMSPACE_H
#define MOCK_AVR_PGMSPACE_H
#include <stdint.h>
#define PROGMEM
#define pgm_read_byte(p) (*(const uint8_t *)(p))
#define pgm_read_word(p) (*(const uint16_t *)(p))
#endif
