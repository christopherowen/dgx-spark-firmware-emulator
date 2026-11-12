/* SPDX-License-Identifier: Apache-2.0
 * Original probe: ask the eSPI Service for the six power-limit registers, and nothing else.
 *
 * The registers are the ones the eSPI Service's own image addresses, with 0x06000718 read as a type
 * field and then the rest of the block; that analysis stays local. The call shape is the one the ACPI
 * fan methods use and the one measured working on an ACUM028 unit:
 *
 *   x0 = 0xC400008D, x1 = 0x8003, x2/x3 = the service's two constants, x4 = word
 *   word = 0x0C | (command << 8) | (4 << 40)      (read, four bytes)
 *
 * Two things this probe refuses to do: write anything (opcode 0x09 is not in this file), and ask for a
 * register that is not one of the six. Whether the gateway accepts an id outside the documented fan
 * family is the open question, and this payload exists to answer it with reads if the owner decides to.
 *
 * Output: "PL <register> <value|REFUSED>" per register, then "PL-OK" if all six answered.
 */
typedef unsigned short char16;
#ifdef REPORT_VAR
#include "efi_var.h"          /* every putc also lands in the SparkProbe variable */
#endif

typedef struct {
    void *Reset;
    unsigned long long (*OutputString)(void *, const char16 *);
} text_output;

static text_output *CON;
#define GUID_HI 0x20418532a0634a88ULL
#define GUID_LO 0x46a5a008c0eeaa83ULL

typedef unsigned long long u64;          /* this target's `unsigned long` is 32-bit */

static const u64 registers[] = {
    0x06000714ULL, 0x06000718ULL, 0x0600071cULL, 0x06000720ULL, 0x06000724ULL, 0x06000728ULL,
};

static void putc(char c) {
    char16 text[2] = { (char16)(unsigned char)c, 0 };
    CON->OutputString(CON, text);
#ifdef REPORT_VAR
    report_char(c);
#endif
}

static void puts(const char *text) { while (*text) putc(*text++); }

static void hex(u64 value) {
    const char *digits = "0123456789abcdef";
    for (int shift = 60; shift >= 0; shift -= 4)
        putc(digits[(value >> shift) & 0xf]);
}

static int allowed(u64 command) {
    for (unsigned i = 0; i < sizeof(registers) / sizeof(registers[0]); i++)
        if (registers[i] == command)
            return 1;
    return 0;
}

static int read_register(u64 command, u64 *value) {
    register u64 x0 __asm__("x0") = 0xc400008dULL;
    register u64 x1 __asm__("x1") = 0x8003ULL;
    register u64 x2 __asm__("x2") = GUID_HI;
    register u64 x3 __asm__("x3") = GUID_LO;
    register u64 x4 __asm__("x4") = 0x0cULL | (command << 8) | (4ULL << 40);
    if (!allowed(command))
        return 0;
    __asm__ volatile("smc #0"
                     : "+r"(x0), "+r"(x1), "+r"(x2), "+r"(x3), "+r"(x4)
                     :
                     : "x5", "x6", "x7", "x8", "x9", "x10", "x11", "x12", "x13", "x14", "x15",
                       "x16", "x17", "memory");
    if (x0 != 0xc400008eULL)
        return 0;
    *value = x4;
    return 1;
}

static int run(void) {
    int ok = 1;
    for (unsigned i = 0; i < sizeof(registers) / sizeof(registers[0]); i++) {
        u64 value = 0;
        puts("PL ");
        hex(registers[i]);
        if (read_register(registers[i], &value)) {
            puts(" ");
            hex(value);
        } else {
            puts(" REFUSED");
            ok = 0;
        }
        putc('\n');
    }
    puts(ok ? "PL-OK\n" : "PL-REFUSED\n");
    return ok;
}

unsigned long long _start(void *image, void *system_table) {
    (void)image;
    CON = *(text_output **)((unsigned char *)system_table + 0x40);
#ifdef REPORT_VAR
    report_init(system_table);
#endif
    if (!CON)
        return 1;
    int result = run();
#ifdef REPORT_VAR
    report_flush();
#endif
    return result ? 0 : 2;
}
