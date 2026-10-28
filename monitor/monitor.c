/* SPDX-License-Identifier: Apache-2.0
 * Original experimental Spark firmware laboratory monitor.
 */
#include "nor.h"

typedef unsigned long u64;
typedef unsigned int u32;
typedef unsigned short u16;
#define READ(reg)                                                                                  \
    ({                                                                                             \
        u64 x;                                                                                     \
        __asm__ volatile("mrs %0, " #reg : "=r"(x));                                               \
        x;                                                                                         \
    })
#define WRITE(reg, x) __asm__ volatile("msr " #reg ", %0" ::"r"((u64)(x)) : "memory")
#define SCR ((1UL << 10) | (1UL << 11) | (1UL << 8) | (1UL << 18))
#define SUCCESS 0x84000061UL
#define ERROR 0x84000060UL
#define DIRECT_REQ 0xc400006fUL
#define DIRECT_RESP 0xc4000070UL
static void console_puts(const char *s) {
    while (*s) {
        *(volatile u32 *)0x16a00000 = *s++;
    }
}
static void hex(u64 x) {
    char h[19] = "0x0000000000000000";
    for (int i = 0; i < 16; i++)
        h[17 - i] = "0123456789abcdef"[(x >> (i * 4)) & 15];
    console_puts(h);
}
static void copy(u64 *d, const u64 *s, int count) {
    for (int i = 0; i < count; i++)
        d[i] = s[i];
}
static void clear(u64 *r) {
    for (int i = 0; i < 8; i++)
        r[i] = 0;
}
static void success(u64 *r) {
    clear(r);
    r[0] = SUCCESS;
}
static void unsupported(u64 *r) {
    clear(r);
    r[0] = ERROR;
    r[2] = -1UL;
}
struct context {
    u64 r[31], pc, pstate;
    u64 SP_EL0;
    u64 SP_EL1;
    u64 SP_EL2;
    u64 ELR_EL1;
    u64 SPSR_EL1;
    u64 ELR_EL2;
    u64 SPSR_EL2;
    u64 TTBR0_EL1;
    u64 TTBR1_EL1;
    u64 TCR_EL1;
    u64 MAIR_EL1;
    u64 SCTLR_EL1;
    u64 VBAR_EL1;
    u64 CPACR_EL1;
    u64 TTBR0_EL2;
    u64 TCR_EL2;
    u64 MAIR_EL2;
    u64 SCTLR_EL2;
    u64 VBAR_EL2;
    u64 HCR_EL2;
    u64 CPTR_EL2;
    u64 VTTBR_EL2;
    u64 VTCR_EL2;
    u64 TPIDR_EL1;
    u64 TPIDR_EL0;
    u64 TPIDRRO_EL0;
    u64 CNTKCTL_EL1;
    u64 CNTHCTL_EL2;
    u64 CONTEXTIDR_EL1;
    u64 fp[66] __attribute__((aligned(16)));
};
static struct context secure_context, nonsecure_context;
static int ns_started, ns_pending;
static u64 ns_rx, ns_tx, sp_rx, sp_tx;
static u64 *sp_hvc_frame;
static u64 shared_address, shared_size;
static int ns_rx_full, sp_rx_full;

/* Lab RAM only. Monitor stacks and the NOR backing are never message buffers. */
static int guest_buffer(u64 address, u64 size) {
    return size && address >= 0x80100000UL && size <= 0x3ff00000UL &&
           address <= 0xc0000000UL - size &&
           (address + size <= 0xa0000000UL || address >= 0xa0100000UL);
}

extern void enter(u64 ignored, u64 arg0);
extern void save_fp(u64 *p);
extern void restore_fp(u64 *p);
static void save(struct context *c, u64 *r) {
    save_fp(c->fp);
    copy(c->r, r, 31);
    c->pc = READ(ELR_EL3);
    c->pstate = READ(SPSR_EL3);
    c->SP_EL0 = READ(SP_EL0);
    c->SP_EL1 = READ(SP_EL1);
    c->SP_EL2 = READ(SP_EL2);
    c->ELR_EL1 = READ(ELR_EL1);
    c->SPSR_EL1 = READ(SPSR_EL1);
    c->ELR_EL2 = READ(ELR_EL2);
    c->SPSR_EL2 = READ(SPSR_EL2);
    c->TTBR0_EL1 = READ(TTBR0_EL1);
    c->TTBR1_EL1 = READ(TTBR1_EL1);
    c->TCR_EL1 = READ(TCR_EL1);
    c->MAIR_EL1 = READ(MAIR_EL1);
    c->SCTLR_EL1 = READ(SCTLR_EL1);
    c->VBAR_EL1 = READ(VBAR_EL1);
    c->CPACR_EL1 = READ(CPACR_EL1);
    c->TTBR0_EL2 = READ(TTBR0_EL2);
    c->TCR_EL2 = READ(TCR_EL2);
    c->MAIR_EL2 = READ(MAIR_EL2);
    c->SCTLR_EL2 = READ(SCTLR_EL2);
    c->VBAR_EL2 = READ(VBAR_EL2);
    c->HCR_EL2 = READ(HCR_EL2);
    c->CPTR_EL2 = READ(CPTR_EL2);
    c->VTTBR_EL2 = READ(VTTBR_EL2);
    c->VTCR_EL2 = READ(VTCR_EL2);
    c->TPIDR_EL1 = READ(TPIDR_EL1);
    c->TPIDR_EL0 = READ(TPIDR_EL0);
    c->TPIDRRO_EL0 = READ(TPIDRRO_EL0);
    c->CNTKCTL_EL1 = READ(CNTKCTL_EL1);
    c->CNTHCTL_EL2 = READ(CNTHCTL_EL2);
    c->CONTEXTIDR_EL1 = READ(CONTEXTIDR_EL1);
}
static void restore(struct context *c, u64 *r) {
    WRITE(SCTLR_EL2, READ(SCTLR_EL2) & ~1UL);
    WRITE(SCTLR_EL1, READ(SCTLR_EL1) & ~1UL);
    __asm__ volatile("isb");
    WRITE(SP_EL0, c->SP_EL0);
    WRITE(SP_EL1, c->SP_EL1);
    WRITE(SP_EL2, c->SP_EL2);
    WRITE(ELR_EL1, c->ELR_EL1);
    WRITE(SPSR_EL1, c->SPSR_EL1);
    WRITE(ELR_EL2, c->ELR_EL2);
    WRITE(SPSR_EL2, c->SPSR_EL2);
    WRITE(TTBR0_EL1, c->TTBR0_EL1);
    WRITE(TTBR1_EL1, c->TTBR1_EL1);
    WRITE(TCR_EL1, c->TCR_EL1);
    WRITE(MAIR_EL1, c->MAIR_EL1);
    WRITE(VBAR_EL1, c->VBAR_EL1);
    WRITE(CPACR_EL1, c->CPACR_EL1);
    WRITE(TTBR0_EL2, c->TTBR0_EL2);
    WRITE(TCR_EL2, c->TCR_EL2);
    WRITE(MAIR_EL2, c->MAIR_EL2);
    WRITE(VBAR_EL2, c->VBAR_EL2);
    WRITE(HCR_EL2, c->HCR_EL2);
    WRITE(CPTR_EL2, c->CPTR_EL2);
    WRITE(VTTBR_EL2, c->VTTBR_EL2);
    WRITE(VTCR_EL2, c->VTCR_EL2);
    WRITE(TPIDR_EL1, c->TPIDR_EL1);
    WRITE(TPIDR_EL0, c->TPIDR_EL0);
    WRITE(TPIDRRO_EL0, c->TPIDRRO_EL0);
    WRITE(CNTKCTL_EL1, c->CNTKCTL_EL1);
    WRITE(CNTHCTL_EL2, c->CNTHCTL_EL2);
    WRITE(CONTEXTIDR_EL1, c->CONTEXTIDR_EL1);

    WRITE(SCTLR_EL1, c->SCTLR_EL1);
    WRITE(SCTLR_EL2, c->SCTLR_EL2);
    __asm__ volatile("isb;clrex");
    restore_fp(c->fp);
    copy(r, c->r, 31);
    WRITE(ELR_EL3, c->pc);
    WRITE(SPSR_EL3, c->pstate);
}
static void yield(u64 *r, int response) {
    sp_hvc_frame = r;
    register u64 x0 __asm__("x0") = 0xc000fffeUL;
    register u64 x1 __asm__("x1") = response;
    __asm__ volatile("smc #0"
                     : "+r"(x0), "+r"(x1)::"x2", "x3", "x4", "x5", "x6", "x7", "x8", "x9", "x10",
                       "x11", "x12", "x13", "x14", "x15", "x16", "x17", "memory");
}
void boot(void) {
    console_puts("\nMON: boot STMM\n");
    *(volatile u32 *)0x06880014 = 0;
    WRITE(SCR_EL3, SCR);
    WRITE(HCR_EL2, 1UL << 31);
    WRITE(CPTR_EL2, 0);
    WRITE(VBAR_EL2, READ(VBAR_EL3));
    WRITE(SP_EL2, 0x800e0000);
    WRITE(SCTLR_EL1, 0x30d00800);
    WRITE(SP_EL1, 0x93c00000);
    WRITE(ELR_EL3, 0x93a42000);
    WRITE(SPSR_EL3, 0x3c5);
    enter(0, 0x680000);
}
static void logcall(u64 *r, u64 level, u64 esr) {
    console_puts("\nMON: EL");
    hex(level);
    console_puts(" pc=");
    hex(level == 3 ? READ(ELR_EL3) : READ(ELR_EL2));
    console_puts(" esr=");
    hex(esr);
    for (int i = 0; i < 8; i++) {
        console_puts(" ");
        hex(r[i]);
    }
    console_puts("\n");
}
static u64 rtc_seconds = 946684800, rtc_counter;
static unsigned bcd(unsigned value) { return ((value / 10) << 4) | (value % 10); }
static unsigned unbcd(unsigned value) { return (value >> 4) * 10 + (value & 15); }
static unsigned days_in_month(unsigned y, unsigned m) {
    static const unsigned days[] = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
    return days[m - 1] + (m == 2 && !(y % 4) && (y % 100 || !(y % 400)));
}
static u64 rtc_now(void) {
    return rtc_seconds + (READ(CNTPCT_EL0) - rtc_counter) / READ(CNTFRQ_EL0);
}
static u64 rtc_bcd(void) {
    u64 n = rtc_now(), days = n / 86400;
    unsigned y = 1970, m = 1;
    while (days >= 365 + (!(y % 4) && (y % 100 || !(y % 400)))) {
        days -= 365 + (!(y % 4) && (y % 100 || !(y % 400)));
        y++;
    }
    while (days >= days_in_month(y, m)) {
        days -= days_in_month(y, m);
        m++;
    }
    return bcd(n % 60) | (u64)bcd(n / 60 % 60) << 8 | (u64)bcd(n / 3600 % 24) << 16 |
           (u64)bcd(days + 1) << 24 | (u64)bcd(m) << 32 | (u64)bcd(y - 2000) << 40;
}
static int rtc_set(u64 packed) {
    unsigned y = 2000 + unbcd(packed >> 8 & 255), m = unbcd(packed >> 24 & 255),
             d = unbcd(packed >> 32 & 255), h = unbcd(packed >> 40 & 255),
             min = unbcd(packed >> 48 & 255), sec = unbcd(packed >> 56 & 255);
    if (m < 1 || m > 12 || d < 1 || d > days_in_month(y, m) || h > 23 || min > 59 || sec > 59)
        return 0;
    u64 days = 0;
    for (unsigned i = 1970; i < y; i++)
        days += 365 + (!(i % 4) && (i % 100 || !(i % 400)));
    for (unsigned i = 1; i < m; i++)
        days += days_in_month(y, i);
    rtc_seconds = (days + d - 1) * 86400 + h * 3600 + min * 60 + sec;
    rtc_counter = READ(CNTPCT_EL0);
    return 1;
}
static int rtc_service(u64 *r) {
    u64 data = 0;
    if (r[2] == 0x20418532a0634a88UL && r[3] == 0x46a5a008c0eeaa83UL) {
        u64 request = r[4];
        if (request == 0x10600078e0cUL)
            data = 0;
        else if (request == 0x6060007880cUL)
            data = rtc_bcd();
        else
            return 0;
    } else if (r[2] == 0xea4693b5ed63ea23UL && r[3] == 0x2fe988df248927b0UL) {
        if ((r[4] & 255) != 3 || !rtc_set(r[4]))
            return 0;
    } else
        return 0;
    u64 endpoint = r[1];
    for (int i = 0; i < 18; i++)
        r[i] = 0;
    r[0] = 0xc400008e;
    r[1] = ((endpoint & 0xffff) << 16) | ((endpoint >> 16) & 0xffff);
    r[4] = data;
    return 1;
}
void handle(u64 *r, u64 level) {
    u64 esr = level == 3 ? READ(ESR_EL3) : READ(ESR_EL2);
    u32 fid = r[0];
    int ns = level == 3 && (READ(SCR_EL3) & 1);
    if (fid == 0xc400008a && esr >> 26 == 0x16) {
        u64 n = r[1];
        if (n > 48)
            n = 48;
        for (u64 i = 0; i < n; i++)
            *(volatile u32 *)0x16a00000 = (r[2 + i / 8] >> (8 * (i % 8))) & 255;
        r[0] = 0;
        return;
    }
    if (esr >> 26 != 0x17 && esr >> 26 != 0x16) {
        logcall(r, level, esr);
        console_puts("MON: abort FAR=");
        hex(level == 3 ? READ(FAR_EL3) : READ(FAR_EL2));
        for (;;) {
        }
    }
    if (level == 3 && !ns && fid == 0xc000fffe) {
        save(&secure_context, r);
        if (!ns_started) {
            ns_started = 1;
            console_puts("MON: enter Spark UEFI\n");
            WRITE(SCR_EL3, SCR | 1);
            WRITE(HCR_EL2, 1UL << 31);
            WRITE(CPTR_EL2, 0);
            WRITE(SCTLR_EL2, 0x30c50830);
            WRITE(SP_EL2, 0x800d0000);
            WRITE(ELR_EL3, 0x80800000);
            WRITE(SPSR_EL3, 0x3c9);
            for (int i = 0; i < 31; i++)
                r[i] = 0;
            r[0] = 0x80010000;
        } else if (ns_pending) {
            copy(nonsecure_context.r, sp_hvc_frame, 8);
            WRITE(SCR_EL3, SCR | 1);
            restore(&nonsecure_context, r);
            ns_pending = 0;
        } else {
            console_puts("MON: bad secure yield\n");
            for (;;) {
            }
        }
        return;
    }
    if (fid == 0xc400008d && ns && (r[1] & 0xffff) == 0x8003 && rtc_service(r))
        return;
    if (fid == 0x84000063) {
        r[0] = 0x10002;
        return;
    }
    if (fid == 0x80000000) {
        r[0] = 0x10002;
        return;
    }
    if (fid == 0x84000000) {
        r[0] = 0x10001;
        return;
    }
    if (fid == 0x8400000a) {
        r[0] = -1UL;
        return;
    }
    if (fid == 0x84000069) {
        success(r);
        r[2] = ns ? 0 : 0x8004;
        return;
    }
    if (fid == 0xc4000066) {
        u64 pages = r[3];
        if (pages != 1 || (r[1] & 4095) || (r[2] & 4095) || !guest_buffer(r[1], 4096) ||
            !guest_buffer(r[2], 4096) || r[1] == r[2] || (ns ? ns_tx : sp_tx)) {
            unsupported(r);
            return;
        }
        if (ns) {
            ns_tx = r[1];
            ns_rx = r[2];
        } else {
            sp_tx = r[1];
            sp_rx = r[2];
        }
        success(r);
        return;
    }
    if (fid == 0x84000073) {
        u64 tx = ns ? ns_tx : sp_tx;
        u64 *t = (u64 *)tx;
        if (ns || !tx || r[1] != 96 || r[2] != 96 || (u32)t[3] != 16 || (t[3] >> 32) != 1 ||
            t[4] != 48 || (t[6] >> 32) != 64 || (u16)t[6] != 0x8002 || (t[8] >> 32) != 1 ||
            t[11] > 16 || !t[11]) {
            unsupported(r);
            return;
        }
        u64 address = t[10], size = (u32)t[11] * 4096UL;
        if (!guest_buffer(address, size) || (address & 4095) || shared_address ||
            (u16)t[0] != 0x8004 || t[8] != (1UL << 32 | (u32)t[11])) {
            unsupported(r);
            return;
        }
        shared_address = address;
        shared_size = size;
        success(r);
        r[2] = 1;
        return;
    }
    if (fid == 0x84000077) { /* MEM_RECLAIM: one bounded, synchronous flash share. */
        if (ns || !shared_address || r[1] != 1 || r[2] || r[3]) {
            unsupported(r);
            return;
        }
        shared_address = shared_size = 0;
        success(r);
        return;
    }
    if (fid == DIRECT_REQ && (r[1] & 0xffff) == 0x8002) {
        unsigned char *b = (unsigned char *)shared_address;
        if (ns || r[1] != 0x80048002 || !shared_address || r[3] != 1 || r[4] != shared_size) {
            unsupported(r);
            return;
        }
        if (!nor_request((unsigned char *)0xa0000000UL, NOR_SIZE, b, shared_size)) {
            unsupported(r);
            return;
        }
        u64 endpoints = r[1];
        clear(r);
        r[0] = DIRECT_RESP;
        r[1] = ((endpoints & 0xffff) << 16) | ((endpoints >> 16) & 0xffff);
        return;
    }
    if (fid == 0x84000068) {
        u64 rx = ns ? ns_rx : sp_rx;
        if ((r[1] || r[2] || r[3] || r[4]) && (r[1] != 0x8afb129b || r[2] != 0x64ca4760 ||
                                               r[3] != 0x8618c888 || r[4] != 0x4caa6c4a)) {
            unsupported(r);
            return;
        }
        int *full = ns ? &ns_rx_full : &sp_rx_full;
        if (!rx || *full || r[5]) {
            unsupported(r);
            return;
        }
        u32 *data = (u32 *)rx;
        *full = 1;
        data[0] = 0x00018004;
        data[1] = 0x603;
        data[2] = 0x8afb129b;
        data[3] = 0x64ca4760;
        data[4] = 0x8618c888;
        data[5] = 0x4caa6c4a;
        success(r);
        r[2] = 1;
        r[3] = 24;
        return;
    }
    if (fid == 0x84000065) {
        int *full = ns ? &ns_rx_full : &sp_rx_full;
        if (!*full) {
            unsupported(r);
            return;
        }
        *full = 0;
        success(r);
        return;
    }
    if (fid == 0x84000067) {
        if (ns) {
            ns_rx = ns_tx = 0;
        } else {
            sp_rx = sp_tx = 0;
        }
        success(r);
        return;
    }
    if (fid == 0x8400006b && level == 2) {
        console_puts("MON: STMM waiting\n");
        yield(r, 0);
        return;
    }
    if (fid == DIRECT_RESP && level == 2) {
        yield(r, 1);
        return;
    }
    if (fid == DIRECT_REQ && ns && (r[1] & 0xffff) == 0x8004) {
        if (ns_pending || !sp_hvc_frame || r[1] >> 16) {
            unsupported(r);
            return;
        }
        save(&nonsecure_context, r);
        ns_pending = 1;
        copy(sp_hvc_frame, r, 8);
        WRITE(SCR_EL3, SCR);
        restore(&secure_context, r);
        return;
    }
    if ((fid & 0xffff) >= 0x60 && (fid & 0xffff) <= 0x8f)
        unsupported(r);
    else
        r[0] = -1UL;
}
