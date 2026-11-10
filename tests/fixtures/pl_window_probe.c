/* SPDX-License-Identifier: Apache-2.0
 * Original probe: raise a power limit in the modelled SPBM window and put it back.
 *
 * The window is reachable over SMCCC 0xC4000090 (a lab id, not a vendor one): x1 selects read or
 * write, x2 is the field offset, x3 the value. The offsets are the firmware's own — PL1's OS slot at
 * 0x100 and its effective value at 0x160 — and the probe prints the effective value before, after
 * raising it and after restoring, so the test can require exactly that sequence.
 *
 * Delivered as an EFI application over the PXE lane.
 */
typedef unsigned long long u64;
typedef unsigned short char16;
typedef struct {
    void *Reset;
    unsigned long long (*OutputString)(void *, const char16 *);
} text_output;

#define SPBM_FID 0xc400008dUL
#define SPBM_ENDPOINT 0x8003UL
#define SPBM_GUID_HI 0x5350424d57494e31UL
#define SPBM_GUID_LO 0x4c41425a4b4c3130UL
#define SPBM_COMPLETION 0xc400008eUL
#define PL1_OS 0x100
#define PL1_EFFECTIVE 0x160
#define RAISED 45000UL

static text_output *CON;

static void putc(char c)
{
    char16 buffer[2] = { (char16)(unsigned char)c, 0 };
    CON->OutputString(CON, buffer);
}

static void putn(u64 value)
{
    char buffer[24];
    int n = 0;
    if (!value) buffer[n++] = '0';
    while (value) { buffer[n++] = (char)('0' + value % 10); value /= 10; }
    while (n) putc(buffer[--n]);
}

/* unsigned long is 32-bit on this target's ABI, so the packed request needs the explicit 64-bit
 * type above: shifting an offset by 8 or a value by 40 in a long truncates it. */
static u64 call(u64 op, u64 offset, u64 value)
{
    /* The OEM function id the firmware forwards, a lab GUID of our own, and the request word packed
     * the way the other services on this id pack theirs. */
    u64 request = op | (offset << 8) | (value << 40);
    u64 frame[5] = { SPBM_FID, SPBM_ENDPOINT, SPBM_GUID_HI, SPBM_GUID_LO, request };
    __asm__ volatile("ldp x0, x1, [%0]\n"
                     "ldp x2, x3, [%0, #16]\n"
                     "ldr x4, [%0, #32]\n"
                     "smc #0\n"
                     "stp x0, x1, [%0]\n"
                     "stp x2, x3, [%0, #16]\n"
                     "str x4, [%0, #32]\n"
                     :
                     : "r"(frame)
                     : "x0", "x1", "x2", "x3", "x4", "x5", "x6", "x7", "x8", "x9", "x10",
                       "x11", "x12", "x13", "x14", "x15", "x16", "x17", "memory");
    return frame[0] == SPBM_COMPLETION ? frame[4] : 0xffffffffffffffffUL;
}

static u64 read_effective(void)
{
    return call(0, PL1_EFFECTIVE, 0);
}

static void write_os(u64 value)
{
    (void)call(1, PL1_OS, value);
}

unsigned long long _start(void *image, void *system_table)
{
    (void)image;
    CON = *(text_output **)((unsigned char *)system_table + 0x40);
    if (!CON)
        return 1;
    u64 before = read_effective();
    putc('B'); putc(' '); putn(before); putc('\n');
    write_os(RAISED);
    u64 os_value = call(0, PL1_OS, 0);
    putc('O'); putc(' '); putn(os_value); putc('\n');
    u64 after = read_effective();
    putc('A'); putc(' '); putn(after); putc('\n');
    write_os(before);
    u64 restored = read_effective();
    putc('R'); putc(' '); putn(restored); putc('\n');
    const char *verdict = (after == RAISED && restored == before) ? "PL-OK\n" : "PL-FAIL\n";
    while (*verdict) putc(*verdict++);
    return (after == RAISED && restored == before) ? 0 : 2;
}
