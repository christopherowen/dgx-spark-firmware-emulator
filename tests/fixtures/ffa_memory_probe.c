/* SPDX-License-Identifier: Apache-2.0
 * Original synthetic clients: execute the actual monitor SMC/HVC interfaces.
 */
typedef unsigned long u64;
#define OK 0x84000061UL
#define ERROR 0x84000060UL
#define REQUIRE(value) do { if (!(value)) fail(__LINE__); } while (0)

static void puts(const char *p) {
    while (*p) *(volatile unsigned *)0x16a00000 = *p++;
}
static void fail(unsigned line) {
    puts("MEMORY FAIL ");
    for (int i = 3; i >= 0; i--)
        *(volatile unsigned *)0x16a00000 = "0123456789abcdef"[line >> (i * 4) & 15];
    puts("\n");
    for (;;) __asm__ volatile("wfi");
}
static void invoke(u64 *r) {
    register u64 x0 __asm__("x0") = r[0], x1 __asm__("x1") = r[1];
    register u64 x2 __asm__("x2") = r[2], x3 __asm__("x3") = r[3];
    register u64 x4 __asm__("x4") = r[4], x5 __asm__("x5") = r[5];
    register u64 x6 __asm__("x6") = r[6], x7 __asm__("x7") = r[7];
#ifdef SECURE_PROBE
    __asm__ volatile("hvc #0"
#else
    __asm__ volatile("smc #0"
#endif
                     : "+r"(x0), "+r"(x1), "+r"(x2), "+r"(x3),
                       "+r"(x4), "+r"(x5), "+r"(x6), "+r"(x7)
                     :: "x8", "x9", "x10", "x11", "x12", "x13", "x14", "x15",
                        "x16", "x17", "cc", "memory");
    r[0] = x0; r[1] = x1; r[2] = x2; r[3] = x3;
    r[4] = x4; r[5] = x5; r[6] = x6; r[7] = x7;
}
static void simple(u64 fid, u64 a, u64 b, u64 c, u64 expected, long error) {
    u64 r[8] = {fid, a, b, c, 0, 0, 0, 0};
    invoke(r);
    REQUIRE(r[0] == expected);
    if (expected == ERROR) REQUIRE(r[2] == (u64)error);
}
static void zero(u64 *p, unsigned n) {
    while (n--) *p++ = 0;
}
static void descriptor(u64 *d, u64 sender, u64 receiver, u64 address) {
    zero(d, 12);
    d[0] = (0x2fUL << 16) | sender;
    d[2] = 42;
    d[3] = (1UL << 32) | 16;
    d[4] = 48;
    d[6] = (64UL << 32) | (6UL << 16) | receiver;
    d[8] = (1UL << 32) | 1;
    d[10] = address;
    d[11] = 1;
}

void memory_probe(void) {
#ifdef SECURE_PROBE
    u64 *tx = (u64 *)0x94140000, *rx = (u64 *)0x94141000;
    simple(0xc4000066, (u64)tx, (u64)rx, 1, OK, 0);
    u64 features[8] = {0x84000064, 0xc4000074, 2, 0, 0, 0, 0, 0};
    invoke(features); REQUIRE(features[0] == OK && features[2] == 2);
    u64 r[8] = {0x8400006b, 0, 0, 0, 0, 0, 0, 0};
    invoke(r);
    for (;;) {
        REQUIRE(r[0] == 0xc400006f && r[1] == 0x8004);
        u64 command = r[3], handle = r[4];
        if (command == 1) {
            /* STMM -> flash traffic must coexist with the active NS share. */
            descriptor(tx, 0x8004, 0x8002, 0x94170000);
            simple(0x84000073, 96, 96, 0, OK, 0);
            unsigned char *flash_request = (unsigned char *)0x94170000;
            for (unsigned i = 0; i < 80; i++) flash_request[i] = 0;
            flash_request[0] = 8; /* Synchronous flush. */
            u64 flash[8] = {0xc400006f, 0x80048002, 0, 1, 4096, 0, 0, 0};
            invoke(flash);
            REQUIRE(flash[0] == 0xc4000070 && !flash_request[5]);
            simple(0x84000077, 1, 0, 0, OK, 0);
            /* An occupied RX mailbox cannot be overwritten by retrieval. */
            simple(0x84000068, 0, 0, 0, OK, 0);
            zero(tx, 12);
            tx[1] = handle; tx[2] = 42; tx[3] = (1UL << 32) | 16;
            tx[4] = 48; tx[6] = 0x8004;
            simple(0xc4000074, 64, 64, 0, ERROR, -4);
            REQUIRE(*(unsigned *)rx == 0x00018004);
            simple(0x84000065, 0, 0, 0, OK, 0);
            u64 retrieve[8] = {0xc4000074, 64, 64, 0, 0, 0, 0, 0};
            invoke(retrieve);
            REQUIRE(retrieve[0] == 0x84000075 && retrieve[1] == 96 && retrieve[2] == 96);
            REQUIRE(rx[0] == (8UL << 32 | 0x6fUL << 16) && rx[1] == handle);
            REQUIRE(rx[10] == 0x88002000 && rx[11] == 1);
            REQUIRE(*(u64 *)rx[10] == 0x12345678);
            *(u64 *)rx[10] = 0x87654321;
            simple(0x84000065, 0, 0, 0, OK, 0);
        } else {
            REQUIRE(command == 2);
            zero(tx, 3); tx[0] = handle; tx[1] = 1UL << 32; tx[2] = 0x8004;
            simple(0x84000076, 0, 0, 0, OK, 0);
        }
        zero(r, 8); r[0] = 0xc4000070; r[1] = 0x80040000; r[3] = command;
        invoke(r);
    }
#else
    u64 *tx = (u64 *)0x88000000;
    simple(0xc4000066, 0x93a40000, 0x88001000, 1, ERROR, -6);
    simple(0xc4000066, (u64)tx, 0x88001000, 1, OK, 0);
    simple(0x84000064, 0xc4000073, 0, 0, OK, 0);
    simple(0x84000064, 0xc4000073, 2, 0, ERROR, -2);
    simple(0x84000064, 0x84000072, 0, 0, ERROR, -1);
    /* NS cannot impersonate STMM or retrieve as the secure borrower. */
    descriptor(tx, 0x8004, 0x8004, 0x88002000);
    simple(0x84000073, 96, 96, 0, ERROR, -6);
    descriptor(tx, 0, 0x8004, 0x88002000);
    simple(0x84000073, 95, 95, 0, ERROR, -2);
    simple(0xc4000074, 64, 64, 0, ERROR, -1);
    *(u64 *)0x88002000 = 0x12345678;
    u64 r[8] = {0xc4000073, 96, 96, 0, 0, 0, 0, 0};
    invoke(r); REQUIRE(r[0] == OK && r[2] > 1 && !r[3]);
    u64 handle = r[2];
    zero(tx, 12); /* Retrieval must use the relayer's immutable copy. */
    simple(0x84000067, 0, 0, 0, OK, 0);
    simple(0xc4000066, 0x88002000, 0x88001000, 1, ERROR, -6);
    simple(0xc4000066, (u64)tx, 0x88001000, 1, OK, 0);
    zero(r, 8); r[0] = 0xc400006f; r[1] = 0x8004; r[3] = 1; r[4] = handle;
    invoke(r); REQUIRE(r[0] == 0xc4000070 && r[3] == 1);
    REQUIRE(*(u64 *)0x88002000 == 0x87654321);
    simple(0x84000077, handle, 0, 0, ERROR, -6);
    zero(r, 8); r[0] = 0xc400006f; r[1] = 0x8004; r[3] = 2; r[4] = handle;
    invoke(r); REQUIRE(r[0] == 0xc4000070 && r[3] == 2);
    simple(0x84000077, handle, 0, 0, OK, 0);
    simple(0x84000077, handle, 0, 0, ERROR, -2);
    descriptor(tx, 0, 0x8004, 0x88002000);
    zero(r, 8); r[0] = 0x84000073; r[1] = r[2] = 96;
    invoke(r); REQUIRE(r[0] == OK && r[2] > handle);
    simple(0x84000077, r[2], 0, 0, OK, 0);
    puts("MEMORY PASS\n");
    for (;;) __asm__ volatile("wfi");
#endif
}
