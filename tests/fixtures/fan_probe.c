/* SPDX-License-Identifier: Apache-2.0
 * Original probe: drive the fan service exactly as the firmware's DSDT does.
 *
 * The DSDT's \_SB.FAN0.WFCF converts a level 0..100 into an rpm and sends it as two little-endian
 * bytes through SMC function 0xC400008D, first word 0x8003, then the two GUID words; \_SB.FAN0._FST
 * reads the rpm back with request 0x0C | (0x06000704 << 8) | (2 << 40). This probe does both and
 * prints what came back, so the emulator's model can be checked against the firmware's arithmetic.
 *
 * Delivered as an EFI application over the PXE lane, because the service lives behind EL3: the
 * monitor owns it, and a probe that runs as verified firmware reaches the same SMC the DSDT does.
 *
 * Output: "F <level> <rpm>" for each level set, then "R0 <rpm>" and "R1 <rpm>" for the two fans'
 * own status commands, then "FAN-OK" or "FAN-FAIL".
 */
#ifdef REPORT_VAR
#include "efi_var.h"          /* every putc also lands in the SparkProbe variable */
#endif

typedef unsigned short char16;
typedef struct {
    void *Reset;
    unsigned long long (*OutputString)(void *, const char16 *);
} text_output;

#define FMIN 975ULL
#define FMAX 4350ULL
#define GUID_HI 0x20418532a0634a88ULL
#define GUID_LO 0x46a5a008c0eeaa83ULL

typedef unsigned long long u64;

static text_output *CON;

static void putc(char c)
{
    char16 buffer[2] = { (char16)(unsigned char)c, 0 };
    CON->OutputString(CON, buffer);
#ifdef REPORT_VAR
    report_char(c);
#endif
}

static void put_hex(u64 value)
{
    static const char digits[] = "0123456789abcdef";
    putc('0'); putc('x');
    for (int shift = 60; shift >= 0; shift -= 4)
        putc(digits[(value >> shift) & 0xf]);
}

#include "fan_smc.h"

/* The allowlist, in one place. A build with READ_ONLY defined carries the two status reads and
 * nothing else: it is the conformance packet, and it cannot write even if a caller asks it to. */
#ifdef READ_ONLY
#define ALLOWED_WRITE(command) 0
#else
#define ALLOWED_WRITE(command) ((command) >= 0x06000790 && (command) <= 0x06000793)
#endif
#define ALLOWED_READ(command) ((command) == 0x06000704 || (command) == 0x06000706)

static int write_byte(u64 command, u64 value)
{
    u64 frame[5] = { 0xc400008d, 0x8003, GUID_HI, GUID_LO,
                     (0x09 | (command << 8)) | (value << 40) };
    if (!ALLOWED_WRITE(command))
        return 0;                       /* refused here, before the call leaves this code */
    return call(frame) == 0xc400008e;
}

/* \_SB.FAN1._FST reads 0x06000706 where \_SB.FAN0._FST reads 0x06000704: one read does not answer
 * for both fans, so the probe asks separately and prints both. */
static int read_fan(u64 command, u64 *rpm)
{
    u64 frame[5] = { 0xc400008d, 0x8003, GUID_HI, GUID_LO,
                     (0x0c | (command << 8)) | (2ULL << 40) };
    if (!ALLOWED_READ(command))
        return 0;                       /* the two documented status reads, no other id */
    if (call(frame) != 0xc400008e)
        return 0;
    *rpm = frame[4] & 0xffff;
    return 1;
}

static void numbers(u64 value)
{
    char buffer[24];
    int n = 0;
    if (!value)
        buffer[n++] = '0';
    while (value) {
        buffer[n++] = (char)('0' + value % 10);
        value /= 10;
    }
    while (n)
        putc(buffer[--n]);
}

static int run(void)
{
    int ok = 1;
#ifdef READ_ONLY
    /* The conformance packet: ask the two questions the DSDT answers, and nothing else. */
    for (unsigned fan = 0; fan < 2; fan++) {
        u64 rpm = 0;
        u64 command = fan ? 0x06000706ULL : 0x06000704ULL;
        if (read_fan(command, &rpm)) {
            putc('R'); putc('0' + (char)fan); putc(' ');
            numbers(rpm);
            putc('\n');
        } else {
            ok = 0;
        }
    }
    const char *packet = ok ? "READ-ONLY-OK\n" : "READ-ONLY-FAIL\n";
    for (const char *c = packet; *c; c++)
        putc(*c);
    return ok ? 0 : 1;
#else
    const unsigned levels[] = { 0, 50, 100 };
    for (unsigned i = 0; i < 3; i++) {
        unsigned level = levels[i];
        u64 rpm = FMIN + ((FMAX - FMIN) * level) / 100;
        ok &= write_byte(0x06000790, rpm & 0xff);
        ok &= write_byte(0x06000791, (rpm >> 8) & 0xff);
        ok &= write_byte(0x06000792, rpm & 0xff);
        ok &= write_byte(0x06000793, (rpm >> 8) & 0xff);
        u64 readback = 0;
        ok &= read_fan(0x06000704, &readback);
        putc('F'); putc(' ');
        numbers(level);
        putc(' ');
        numbers(readback);
        putc('\n');
        if (readback != rpm)
            ok = 0;
    }
    for (unsigned fan = 0; fan < 2; fan++) {
        u64 rpm = 0;
        u64 command = fan ? 0x06000706ULL : 0x06000704ULL;
        if (read_fan(command, &rpm)) {
            putc('R'); putc('0' + (char)fan); putc(' ');
            numbers(rpm);
            putc('\n');
        } else {
            ok = 0;
        }
    }
    const char *verdict = ok ? "FAN-OK\n" : "FAN-FAIL\n";
    while (*verdict) putc(*verdict++);
    return ok;
#endif
}

unsigned long long _start(void *image, void *system_table)
{
    (void)image;
    CON = *(text_output **)((unsigned char *)system_table + 0x40);
#ifdef REPORT_VAR
    report_init(system_table);
#endif
    if (!CON)
        return 1;
    for (;;) {
        int result = run();
#ifdef REPORT_VAR
        report_flush();
#endif
        if (result)
            return 0;
        return 2;
    }
}
