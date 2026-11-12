/* SPDX-License-Identifier: Apache-2.0
 * The SPBM window model: storage for the documented fields, plus the one relationship the vendor's
 * interface table supports — a write to an OS limit slot is mirrored into the effective slot. The
 * real arbitration between OS, EC and UEFI claims lives in the SPBM firmware, which ships encrypted
 * in the only artifact we have, so it is not modelled and must not be implied.
 */
#include "spbm.h"

#define SPBM_FID 0xc400008dUL
#define SPBM_ENDPOINT 0x8003UL
/* A lab GUID of this repository's own: not a vendor service identity. */
#define SPBM_GUID_HI 0x5350424d57494e31UL
#define SPBM_GUID_LO 0x4c41425a4b4c3130UL
#define SPBM_COMPLETION 0xc400008eUL
#define SPBM_OP_READ 0
#define SPBM_OP_WRITE 1

static unsigned char window[SPBM_WINDOW];

static unsigned read32(unsigned offset)
{
    return (unsigned)window[offset] | (unsigned)window[offset + 1] << 8 |
           (unsigned)window[offset + 2] << 16 | (unsigned)window[offset + 3] << 24;
}

static void write32(unsigned offset, unsigned value)
{
    window[offset] = value & 0xff;
    window[offset + 1] = (value >> 8) & 0xff;
    window[offset + 2] = (value >> 16) & 0xff;
    window[offset + 3] = (value >> 24) & 0xff;
}

int spbm_request(spbm_u64 *r)
{
    if (r[0] != SPBM_FID || (r[1] & 0xffff) != SPBM_ENDPOINT)
        return 0;
    if (r[2] != SPBM_GUID_HI || r[3] != SPBM_GUID_LO)
        return 0;
    spbm_u64 request = r[4];
    unsigned op = (unsigned)(request & 0xff);
    unsigned offset = (unsigned)((request >> 8) & 0xffffffffUL);
    unsigned value = (unsigned)((request >> 40) & 0xffff);
    if (offset + 4 > SPBM_WINDOW)
        return 0;
    unsigned result = 0;
    if (op == SPBM_OP_READ) {
        result = read32(offset);
    } else if (op == SPBM_OP_WRITE) {
        write32(offset, value);
        if (offset >= SPBM_PL1_OS && offset <= SPBM_PL4_OS)
            write32(offset - SPBM_PL1_OS + SPBM_PL1_EFFECTIVE, value);
        result = read32(offset);
    } else {
        return 0;
    }
    spbm_u64 endpoint = r[1];
    for (int i = 0; i < 18; i++)
        r[i] = 0;
    r[0] = SPBM_COMPLETION;
    r[1] = ((endpoint & 0xffff) << 16) | ((endpoint >> 16) & 0xffff);
    r[4] = result;
    return 1;
}
