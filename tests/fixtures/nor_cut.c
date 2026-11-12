/* SPDX-License-Identifier: Apache-2.0
 * A test clock for the flash model's program pulse.
 *
 * The monitor spins on the system counter, which cannot be cut at an exact byte. This
 * shim supplies the clock instead: it adds up the microseconds the model asks for and,
 * when the running total reaches a configured cut, jumps out — leaving the flash in
 * exactly the state a reset that landed there would leave, which is the thing a
 * power-loss test needs to inspect.
 */
#include <setjmp.h>

#include "nor.h"

static jmp_buf cut_jump;
static unsigned long spent, cut;
static int armed;

unsigned long nor_test_spent(void) { return spent; }

void nor_delay_hook(unsigned long microseconds) {
    spent += microseconds;
    if (armed && spent >= cut)
        longjmp(cut_jump, 1);
}

/* Returns 1 if the pulse was cut, 0 if the request completed. */
int nor_request_until(unsigned char *flash, unsigned long flash_size, unsigned char *message,
                      unsigned long message_size, int trusted, unsigned long cut_us) {
    spent = 0;
    cut = cut_us;
    armed = 1;
    if (setjmp(cut_jump) == 0) {
        nor_request(flash, flash_size, message, message_size, trusted);
        armed = 0;
        return 0;
    }
    armed = 0;
    return 1;
}

/* The plain entry point, without a cut, for comparability. */
int nor_request_whole(unsigned char *flash, unsigned long flash_size, unsigned char *message,
                      unsigned long message_size, int trusted) {
    return nor_request_until(flash, flash_size, message, message_size, trusted, (unsigned long)-1);
}
