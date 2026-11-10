/* SPDX-License-Identifier: Apache-2.0
 * The fan service behind SMCCC 0xC400008D, modelled from the firmware's own DSDT.
 *
 * The DSDT builds one request shape for every OEM service it uses:
 *
 *   x0 = 0xC400008D, x1 = 0x8003, x2/x3 = the service GUID, x4 = the request word
 *
 * and the answer comes back with x0 = 0xC400008E, the endpoint echoed in x1, and the result in x4.
 * The RTC service claims the same call by its GUID; the fan service is distinguished by the
 * request word \_SB.FAN0.WRB builds:
 *
 * The two 64-bit constants the AML passes in x2/x3 are one FF-A partition UUID, read little-endian:
 * 0x20418532a0634a88 46a5a008c0eeaa83 -> 884a63a0-3285-4120-83aa-eec008a0a546, which a live unit
 * enumerates under partition id 0x8003 (research/ffa-partitions-live.json). The endpoint x1 = 0x8003 is
 * that partition id.
 *
 *   write: x4 = 0x09 | (command << 8) | (value << 40), command 0x06000790..93
 *   read:  x4 = 0x0C | (0x06000704 << 8) | (2 << 40) for fan 0,
 *                0x0C | (0x06000706 << 8) | (2 << 40) for fan 1
 *
 * and by the level arithmetic \_SB.FAN0.WFCF uses, which is reproduced here exactly.
 */
#include "fan.h"

#define FAN_GUID_HI 0x20418532a0634a88ULL
#define FAN_GUID_LO 0x46a5a008c0eeaa83ULL
#define FAN_ENDPOINT 0x8003ULL
#define FAN_COMMAND_BASE 0x06000790ULL
/* Each fan has its own status command: \_SB.FAN0._FST reads 0x06000704 and \_SB.FAN1._FST reads
 * 0x06000706. The read of the two fan speeds is not one call. */
#define FAN_COMMAND_STATUS0 0x06000704ULL
#define FAN_COMMAND_STATUS1 0x06000706ULL
#define FAN_OP_WRITE 0x09
#define FAN_OP_READ 0x0c
#define FAN_COMPLETION 0xc400008eULL

static unsigned char fan_byte[2][2];
static unsigned rpm[2] = { FAN_RPM_MIN, FAN_RPM_MIN };

unsigned fan_rpm(unsigned index) { return index < 2 ? rpm[index] : 0; }

unsigned fan_rpm_for_level(unsigned level)
{
    if (level > 100)
        level = 100;
    return (unsigned)(FAN_RPM_MIN + ((FAN_RPM_MAX - FAN_RPM_MIN) * level) / 100);
}

static void complete(fan_u64 *r)
{
    fan_u64 endpoint = r[1];
    for (int i = 0; i < 18; i++)
        r[i] = 0;
    r[0] = FAN_COMPLETION;
    r[1] = ((endpoint & 0xffff) << 16) | ((endpoint >> 16) & 0xffff);
}

int fan_request(fan_u64 *r)
{
    if (r[0] != 0xc400008dULL || (r[1] & 0xffff) != FAN_ENDPOINT)
        return 0;
    if (r[2] != FAN_GUID_HI || r[3] != FAN_GUID_LO)
        return 0;
    fan_u64 request = r[4];
    unsigned op = (unsigned)(request & 0xff);
    unsigned long command = (unsigned long)((request >> 8) & 0xffffffffULL);
    unsigned value = (unsigned)((request >> 40) & 0xffff);
    if (op == FAN_OP_WRITE) {
        if (command < FAN_COMMAND_BASE || command > FAN_COMMAND_BASE + 3)
            return 0;
        unsigned index = (command - FAN_COMMAND_BASE) >> 1;
        unsigned high = (command - FAN_COMMAND_BASE) & 1;
        fan_byte[index][high] = (unsigned char)value;
        rpm[index] = (unsigned)fan_byte[index][1] << 8 | fan_byte[index][0];
        complete(r);
        return 1;
    }
    if (op == FAN_OP_READ) {
        unsigned index;
        if (command == FAN_COMMAND_STATUS0)
            index = 0;
        else if (command == FAN_COMMAND_STATUS1)
            index = 1;
        else
            return 0;
        complete(r);
        r[4] = rpm[index];
        return 1;
    }
    return 0;
}
