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
#ifdef SPARK_FFA_EC
/* Packet-service UUID in the byte order Linux places in DIRECT_REQ2 x2/x3.
 * This is not the OEM/AML UUID above, despite their shared endpoint number. */
#define PACKET_UUID0 0x86491dd2804db078UL
#define PACKET_UUID1 0xc57a24607b46cb8aUL
static unsigned lower_floor = 0xffff;
static unsigned char packet_reply[65];

unsigned fan_lower_floor(void) { return lower_floor; }

static void put16(unsigned char *p, unsigned value)
{
    p[0] = value;
    p[1] = value >> 8;
}

static void packet_rpm(void)
{
    const unsigned minimum[] = {1260, 1890}, maximum[] = {9000, 13500};
    for (unsigned i = 0; i < 2; i++) {
        /* A deterministic automatic policy, not a thermal/ramp/tachometer simulation. */
        unsigned demand = lower_floor == 0xffff ? minimum[i] : lower_floor;
        if (lower_floor && demand < minimum[i])
            demand = minimum[i];
        rpm[i] = demand > maximum[i] ? maximum[i] : demand;
    }
}

int fan_packet_request(fan_u64 *r)
{
    int v2 = r[0] == 0xc400008dUL;
    if ((r[0] != 0xc400006fUL && !v2) || r[1] != FAN_ENDPOINT ||
        (v2 ? (r[2] != PACKET_UUID0 || r[3] != PACKET_UUID1) : r[2] != 0))
        return 0;
    unsigned start = v2 ? 4 : 3, words = v2 ? 18 : 8;
    unsigned char *raw = (unsigned char *)&r[start];
    unsigned command = raw[0], operation = raw[1], in = raw[2], out = raw[3];
    unsigned value = raw[4] | (unsigned)raw[5] << 8;
    fan_u64 endpoint = r[1];
    for (unsigned i = 0; i < words; i++)
        r[i] = 0;
    r[0] = v2 ? 0xc400008eUL : 0xc4000070UL;
    r[1] = endpoint << 16;
    raw = (unsigned char *)&r[start];
    if (command == 2) {
        unsigned bytes = v2 ? sizeof(packet_reply) : 40;
        for (unsigned i = 0; i < bytes; i++)
            raw[i] = packet_reply[i];
        return 1;
    }
    if (command != 1) {
        raw[0] = 1; /* Unknown packet command: nonzero submit status. */
        return 1;
    }
    if (!((operation == 1 && in == 0 && out == 10) ||
          (operation == 4 && in == 0 && out == 2) ||
          (operation == 5 && in == 2 && out == 0) ||
          (operation == 7 && in == 0 && out == 64))) {
        raw[0] = 1;
        return 1;
    }
    for (unsigned i = 0; i < sizeof(packet_reply); i++)
        packet_reply[i] = 0;
    if (operation == 1) {
        packet_reply[1] = 1; /* capabilities discriminator; RPM unit mode is zero */
        put16(packet_reply + 3, 1260);
        put16(packet_reply + 5, 9000);
        put16(packet_reply + 7, 1890);
        put16(packet_reply + 9, 13500);
    } else if (operation == 4) {
        put16(packet_reply + 1, lower_floor);
    } else if (operation == 5) {
        lower_floor = value;
        packet_rpm();
    } else {
        packet_rpm();
        put16(packet_reply + 5, rpm[0]);
        put16(packet_reply + 7, rpm[1]);
    }
    /* Synchronous model: submit succeeds and every subsequent poll is complete.
     * The cached reply remains available to preflight polls, like the relay ABI. */
    return 1;
}
#endif


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
