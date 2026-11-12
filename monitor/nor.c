/* SPDX-License-Identifier: Apache-2.0
 * Functional variable-partition flash service; no vendor implementation code.
 */
#include "nor.h"

static unsigned long get_le(const unsigned char *p, unsigned count) {
    unsigned long value = 0;
    for (unsigned i = 0; i < count; i++)
        value |= (unsigned long)p[i] << (8 * i);
    return value;
}

static void put_le(unsigned char *p, unsigned long value, unsigned count) {
    for (unsigned i = 0; i < count; i++)
        p[i] = value >> (8 * i);
}

/* Reads are 32-bit: an address above 0xffffffff is refused, and so is a request whose
 * `address + count` carries out of 32 bits or does not survive truncation. The 24-bit address of
 * program and erase does not apply to reads. */
static int wide_enough(unsigned long address, unsigned long count) {
    if (address > 0xffffffffUL)
        return 0;
    unsigned long sum = (address + count) & 0xffffffffUL;
    if (address + count > 0xffffffffUL)
        return 0;
    return sum == address + count;
}

static int contains(unsigned long address, unsigned long size) {
#if NOR_OFFSET > 0
    if (address < NOR_OFFSET)
        return 0;
#endif
    return size <= NOR_SIZE && address - NOR_OFFSET <= NOR_SIZE - size;
}

/* The latch starts armed because the board's own trusted sender arms it during
 * cold boot, before any normal-world component runs. A build may open it to
 * model the pre-arming window.
 *
 * A request is refused only when the latch is set *and* the sender is not the
 * trusted endpoint. Command 8 is that endpoint's arming request and is never
 * accepted from anyone else. */
static int policy_armed = 1;

void nor_set_policy_armed(int armed) { policy_armed = armed != 0; }

int nor_policy_armed(void) { return policy_armed; }

/* Optional write latency.  A real serial NOR takes milliseconds per program or
 * erase; the model is synchronous, so the cheapest honest representation is to
 * spin for the configured time before replying.  Build-time only, default 0,
 * and zero must leave every result unchanged. */
#ifndef NOR_WRITE_US
#define NOR_WRITE_US 0
#endif

/* A sector erase is one to two orders of magnitude slower than a program, so it
 * gets its own budget.  Defaults to the program value to keep old builds
 * byte-identical when only NOR_WRITE_US is supplied. */
#ifndef NOR_ERASE_US
#define NOR_ERASE_US NOR_WRITE_US
#endif

/* Kept out of line so a tracer can break on the program pulse itself; the
 * compiler otherwise folds it into both call sites. */
__attribute__((noinline)) __attribute__((unused)) static void delay_us(unsigned long microseconds) {
#if (NOR_WRITE_US > 0 || NOR_ERASE_US > 0) && !defined(NOR_DELAY_HOOK)
    unsigned long freq, start, now;
    __asm__ volatile("mrs %0, cntfrq_el0" : "=r"(freq));
    __asm__ volatile("mrs %0, cntpct_el0" : "=r"(start));
    if (!freq)
        return;
    do {
        __asm__ volatile("mrs %0, cntpct_el0" : "=r"(now));
    } while ((now - start) < microseconds * (freq / 1000000UL));
#else
    (void)microseconds;
#endif
}

/* How a program pulse is modelled.
 *
 * 0 (`page-before-delay-v1`): the whole request is committed, then the page budget is
 *    spent. A reset that lands during the pulse leaves everything written.
 * 1 (`linear-request-byte-v1`): each physical page's budget is spread across the bytes
 *    that pulse covers, and each byte is committed when its share is spent. A reset
 *    that lands part-way leaves a prefix written — which is what a real part does,
 *    though linearly is an approximation of how the die actually settles.
 */
#ifndef NOR_PROGRAM_MODEL
#define NOR_PROGRAM_MODEL 0
#endif

#ifdef NOR_DELAY_HOOK
/* Test seam: a native test supplies the clock so cuts are exact and fast. */
void nor_delay_hook(unsigned long microseconds);
static void model_delay(unsigned long microseconds) { nor_delay_hook(microseconds); }
#else
static void model_delay(unsigned long microseconds) { delay_us(microseconds); }
#endif

#if NOR_PROGRAM_MODEL == 0
static void write_delay(void) { model_delay(NOR_WRITE_US); }
#endif
static void erase_delay(void) { delay_us(NOR_ERASE_US); }


#ifdef NOR_TRACE
static unsigned trusted_seen;

/* One line per answered request, on the console the monitor already owns. This is a
 * diagnostic build: it changes no decision, only what is printed. */
static void trace_text(const char *text) {
    while (*text)
        *(volatile unsigned *)0x16a00000 = (unsigned char)*text++;
}
static void trace_hex(const char *label, unsigned long value, unsigned digits) {
    static const char hexdigits[] = "0123456789abcdef";
    trace_text(label);
    for (int shift = (digits - 1) * 4; shift >= 0; shift -= 4)
        *(volatile unsigned *)0x16a00000 = hexdigits[(value >> shift) & 15];
}
#endif

/* A refused request still gets an answer: the board replies 0x0A whatever the
 * sender asked for, including a message too short to parse. */

static int deny(unsigned char *b, unsigned long size) {
    if (size < 6)
        return 0;
#ifdef NOR_TRACE
    trace_text("\nFLASH DENY cmd=");
    trace_hex("", b[0], 2);
    trace_hex(" verified=", trusted_seen, 1);
#endif
    unsigned char cmd = b[0];
    unsigned long zero = size < 32 ? size : 32;
    for (unsigned long i = 0; i < zero; i++)
        b[i] = 0;
    b[0] = cmd;
    b[5] = NOR_STATUS_DENY;
    return 1;
}

int nor_request(unsigned char *flash, unsigned long flash_size, unsigned char *b,
                unsigned long size, int trusted) {
    /* The gate is evaluated before any semantics: an untrusted sender is
     * refused with 0x0A before the command, its length or its address is even
     * looked at, and command 8 is never honoured for an untrusted sender. */
#ifdef NOR_TRACE
    trusted_seen = trusted != 0;
#endif
    if (size >= 1 && !trusted && (policy_armed || b[0] == 8))
        return deny(b, size);
    if (flash_size != NOR_SIZE || size < 80)
        return 0;
    unsigned cmd = b[0], subcommand = b[5], status = NOR_STATUS_OK, response = 0;
    unsigned long request = get_le(b + 1, 4), address = get_le(b + 7, 8);
    unsigned long count = get_le(b + 32, 4);
    if (request > size - 32)
        return 0;

    if (cmd == 3 && subcommand == 3 && request == 0) {
        response = 33;
        for (unsigned i = 32; i < 80; i++)
            b[i] = 0;
        b[32] = 1;
        put_le(b + 0x31, NOR_OFFSET, 8);
        put_le(b + 0x39, NOR_SIZE, 8);
    } else if (cmd == 0 && request == 4 && count && count <= NOR_BLOCK && count <= size - 32 &&
               wide_enough(address, count) && contains(address, count)) {
        response = count;
        for (unsigned long i = 0; i < count; i++)
            b[32 + i] = flash[address - NOR_OFFSET + i];
    } else if (cmd == 1 && request && request <= NOR_BLOCK && contains(address, request)) {
        unsigned char *target = flash + address - NOR_OFFSET;
        /* Validate the entire request before changing any bytes. */
        for (unsigned long i = 0; i < request; i++)
            if ((target[i] & b[32 + i]) != b[32 + i])
                status = NOR_STATUS_ERROR;
        if (status == NOR_STATUS_OK) {
            unsigned long done = 0, start = address - NOR_OFFSET;
            while (done < request) {
                /* A part programs one physical page per pulse. Splitting by request length alone
                 * would put a request that starts mid-page, and so spans two pages, inside one
                 * pulse: 400 us for two pages and a reset granularity that is not the part's. */
                unsigned long page = NOR_PAGE - ((start + done) & (NOR_PAGE - 1));
                if (page > request - done)
                    page = request - done;
#if NOR_PROGRAM_MODEL == 1
                /* Spread this page's whole budget over the bytes it covers and commit each byte
                 * when its share is spent. The deadline is cumulative and rounded up, so a byte
                 * is never committed early and 400 us is never spent as 400/256. */
                unsigned long spent = 0;
                for (unsigned long i = 0; i < page; i++) {
                    unsigned long due = (unsigned long)(((unsigned long long)(i + 1) * NOR_WRITE_US
                                                         + page - 1) / page);
                    model_delay(due - spent);
                    spent = due;
                    target[done + i] &= b[32 + done + i];
                }
#else
                for (unsigned long i = 0; i < page; i++)
                    target[done + i] &= b[32 + done + i];
                write_delay();
#endif
                done += page;
            }
        }
    } else if (cmd == 2 && request == 4 && count && !(count & (NOR_BLOCK - 1)) &&
               !(address & (NOR_BLOCK - 1)) && contains(address, count)) {
        /* One sector per erase pulse, like the program path: a reset part-way through a
         * multi-sector request leaves the sectors already erased and the rest untouched. */
        unsigned char *target = flash + address - NOR_OFFSET;
        for (unsigned long done = 0; done < count; done += NOR_BLOCK) {
            for (unsigned long i = 0; i < NOR_BLOCK; i++)
                target[done + i] = 255;
            erase_delay();
        }
    } else if (cmd == 8 && request == 0 && count == 0) {
        /* Trusted-only arm. The board requires a zero payload dword. */
        policy_armed = 1;
    } else {
        status = NOR_STATUS_ERROR;
    }
#ifdef NOR_TRACE
    trace_text("\nFLASH cmd=");
    trace_hex("", cmd, 2);
    trace_hex(" req=", request, 8);
    trace_hex(" addr=", address, 16);
    trace_hex(" count=", count, 8);
    trace_hex(" status=", status, 2);
#endif
    /* RAM-backed writes complete synchronously; FLUSH has no pending work. */
    for (unsigned i = 0; i < 32; i++)
        b[i] = 0;
    b[0] = cmd;
    b[5] = status;
    put_le(b + 1, response, 4);
    return 1;
}
