/* SPDX-License-Identifier: Apache-2.0
 * Original normal-world client for a local, unchanged STMM image.
 */
typedef unsigned long u64;
static void puts(const char *p) {
    while (*p) *(volatile unsigned *)0x16a00000 = *p++;
}
static void require(int value) {
    if (value) return;
    puts("MM BUFFER FAIL\n");
    for (;;) __asm__ volatile("wfi");
}
static void invoke(u64 *r) {
    register u64 x0 __asm__("x0") = r[0], x1 __asm__("x1") = r[1];
    register u64 x2 __asm__("x2") = r[2], x3 __asm__("x3") = r[3];
    register u64 x4 __asm__("x4") = r[4], x5 __asm__("x5") = r[5];
    register u64 x6 __asm__("x6") = r[6], x7 __asm__("x7") = r[7];
    __asm__ volatile("smc #0"
                     : "+r"(x0), "+r"(x1), "+r"(x2), "+r"(x3),
                       "+r"(x4), "+r"(x5), "+r"(x6), "+r"(x7)
                     :: "x8", "x9", "x10", "x11", "x12", "x13", "x14", "x15",
                        "x16", "x17", "cc", "memory");
    r[0] = x0; r[1] = x1; r[2] = x2; r[3] = x3;
    r[4] = x4; r[5] = x5; r[6] = x6; r[7] = x7;
}
static void message(u64 address) {
    u64 *data = (u64 *)address;
    /* Unknown synthetic GUID and an eight-byte payload, no variable mutation. */
    data[0] = 0x0123456789abcdef;
    data[1] = 0xfedcba9876543210;
    data[2] = 8;
    data[3] = 0;
}

void memory_probe(void) {
    u64 discover[8] = {0xc400006f, 0x8004, 0, 0xc0270001, 0, 0, 0, 0};
    invoke(discover);
    require(discover[0] == 0xc4000070 && discover[3] == 0xc4000061);
    require(discover[5] == 0x933e0000 && discover[6] == 0x20000);
    puts("MM BUFFER DISCOVERED 0x933e0000 0x20000\n");
    u64 map[8] = {0xc4000066, 0x88000000, 0x88001000, 1, 0, 0, 0, 0};
    invoke(map); require(map[0] == 0x84000061);
    u64 *tx = (u64 *)0x88000000;
    for (unsigned i = 0; i < 12; i++) tx[i] = 0;
    tx[0] = 0x2f0000; tx[3] = (1UL << 32) | 16; tx[4] = 48;
    tx[6] = (64UL << 32) | (6UL << 16) | 0x8004;
    tx[8] = (1UL << 32) | 1; tx[10] = 0x88002000; tx[11] = 1;
    u64 share[8] = {0x84000073, 96, 96, 0, 0, 0, 0, 0};
    invoke(share); require(share[0] == 0x84000061 && share[2] > 1);
    puts("MM OUTSIDE REGION SHARED\n");
    message(discover[5]);
    u64 valid[8] = {0xc400006f, 0x8004, 0, 0xc4000041, 0, discover[5], 0, 0};
    invoke(valid); require(valid[0] == 0xc4000070 && valid[3] == 0xc4000061);
    puts("MM FIXED BUFFER ACCEPTED\n");
    message(0x88002000);
    puts("MM OUTSIDE BUFFER REQUEST\n");
    u64 outside[8] = {0xc400006f, 0x8004, 0, 0xc4000041, 0, 0x88002000, 0, 0};
    invoke(outside);
    /* The host observes STMM's own rejection before its HVC executes. It
     * deliberately does not manufacture a successful direct response.
     */
    require(0);
}
