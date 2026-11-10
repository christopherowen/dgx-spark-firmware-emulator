/* SPDX-License-Identifier: Apache-2.0
 * Original experimental Spark firmware laboratory monitor.
 */
#include "nor.h"
#include "fan.h"
#include "spbm.h"
#include "ffa_memory.h"

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
/* The SCR_EL3 this profile presents, including the enables for the newer system registers a current
 * Linux kernel programs in its early boot. Without them each write traps to EL3, and an EL3 that
 * does not answer it aborts the boot:
 *
 *   25 ENSCXT    SCXTNUM_EL0/EL1 (per-task context numbers)
 *   27 FGTEN     the fine-grained trap registers
 *   38 HXEN      HCRX_EL2
 *   39 GCSEN     GCSCR_EL1 / GCSCRE0_EL1 — guarded control stack
 *   43 TCR2EN    TCR2_EL1
 *   44 SCTLR2En  SCTLR2_EL2
 *   45 PIEN      PIR_EL1 / PIRE0_EL1 — permission indirection
 *
 * The list was found by making the traps name themselves: QEMU logs the register and the controls
 * when an access is routed to EL3 from EL1 with no visible cause. QEMU masks a bit whose feature the
 * CPU lacks, so the set is safe on a model that implements less. They belong in the value itself
 * because the monitor rewrites SCR_EL3 on the first SMC. */
#define SCR ((1UL << 10) | (1UL << 11) | (1UL << 8) | (1UL << 18) | (1UL << 25) | (1UL << 27) | \
             (1UL << 38) | (1UL << 39) | (1UL << 43) | (1UL << 44) | (1UL << 45))
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
static void ffa_error(u64 *r, int error) {
    clear(r);
    r[0] = ERROR;
    r[2] = (u64)(long)error;
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
static struct ffa_memory ns_memory;
static int ns_rx_full, sp_rx_full;

/* Lab RAM only. Monitor stacks and the NOR backing are never message buffers. */
static int guest_buffer(u64 address, u64 size) {
    return size && address >= 0x80100000UL && size <= 0x3ff00000UL &&
           address <= RAM_TOP - size &&
           (address + size <= 0xa0000000UL ||
            address >= NOR_SEED_BASE + 2 * NOR_SIZE + NOR_BLOCK);
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
static void nor_window_seed(void);

/* In whole-device mode the boot image lives inside the flash: the harness slices it out of the image
 * for the first load, but the loader replays that file on every reset, so without this copy a code
 * byte the firmware programmed would be visible to the first boot and then silently reverted. Copy it
 * out of the live window each time the monitor starts, before the non-secure world runs. */
static void code_from_flash(void) {
#ifdef SPARK_CODE_OFFSET
#if SPARK_CODE_OFFSET + SPARK_CODE_SIZE > NOR_SIZE
#error "the boot image must fit inside the modelled part"
#endif
    const unsigned char *source = (const unsigned char *)NOR_LIVE_BASE + SPARK_CODE_OFFSET;
    unsigned char *destination = (unsigned char *)0x80800000UL;
    unsigned long words = SPARK_CODE_SIZE / 8, tail = SPARK_CODE_SIZE % 8, i;
    for (i = 0; i < words; i++)
        ((unsigned long long *)destination)[i] = ((const unsigned long long *)source)[i];
    for (i = words * 8; i < words * 8 + tail; i++)
        destination[i] = source[i];
#endif
}

void boot(void) {
#ifdef SPARK_NOR_POLICY_OPEN
    nor_set_policy_armed(0);
#endif
#ifdef SPARK_GWDT_TIMEOUT_MS
    /* A board BL31 hands the non-secure world a watchdog that already carries a timeout, so the
     * firmware's own "enable" (WCS=1, no WOR write) starts a real interval rather than a
     * zero-length one. Arm WOR here and leave WCS clear: enabling stays the firmware's decision.
     */
    {
        unsigned long long ticks = (unsigned long long)SPARK_GWDT_TIMEOUT_MS * 1000000ULL;
        *(volatile u32 *)(0x1c042000UL + 0x08) = (u32)ticks;
        *(volatile u32 *)(0x1c042000UL + 0x0c) = (u32)(ticks >> 32);
    }
#endif
    nor_window_seed();
    code_from_flash();
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
/* Registers the secure world answers but does not implement. Keyed by the instruction with its
 * read/write bit and its target register removed, so a write and a later read of the same register
 * find each other. */
typedef struct {
    u32 key;
    u64 value;
    u32 accesses;
} sysreg_entry;
#define SYSREG_ENTRIES 16
static sysreg_entry sysregs[SYSREG_ENTRIES];

/* The register is named by the syndrome, not by the instruction the guest was executing: reading
 * that instruction from EL3 means dereferencing a guest virtual address with translation off, and
 * the abort that caused (FAR at a kernel address, EL3 faulting) is exactly what this avoids. */
static void sysreg_access(u64 *r, u64 esr) {
    u32 iss = (u32)(esr & 0x1ffffffu);
    unsigned op0 = (iss >> 20) & 0x3;
    unsigned op2 = (iss >> 17) & 0x7;
    unsigned op1 = (iss >> 14) & 0x7;
    unsigned crn = (iss >> 10) & 0xf;
    unsigned rt = (iss >> 5) & 0x1f;
    unsigned crm = (iss >> 1) & 0xf;
    int read = (iss >> 0) & 1;
    u32 key = ((u32)op0 << 16) | ((u32)op1 << 12) | ((u32)crn << 8) | ((u32)crm << 4) | op2;
    unsigned target = rt;
    for (int i = 0; i < SYSREG_ENTRIES; i++) {
        if (sysregs[i].key == key || sysregs[i].key == 0) {
            if (sysregs[i].key == 0) {
                sysregs[i].key = key;
                sysregs[i].value = 0;
            }
            if (read) {
                r[target] = sysregs[i].value;
            } else {
                sysregs[i].value = r[target];
            }
            if (sysregs[i].accesses++ == 0) {
                console_puts("\nMON: sysreg answered S");
                hex(op0);
                console_puts("_");
                hex(op1);
                console_puts("_C");
                hex(crn);
                console_puts("_C");
                hex(crm);
                console_puts("_");
                hex(op2);
                console_puts(read ? " read" : " write");
                if (!read) {
                    console_puts(" value=");
                    hex(r[target]);
                }
                console_puts(" hcr=");
                hex(READ(HCR_EL2));
                console_puts(" scr=");
                hex(READ(SCR_EL3));
                console_puts(" cptr3=");
                hex(READ(CPTR_EL3));
                console_puts(" vbar2=");
                hex(READ(VBAR_EL2));
                console_puts(" vbar3=");
                hex(READ(VBAR_EL3));
                console_puts(" esr2=");
                hex(READ(ESR_EL2));
                console_puts(" esr=");
                hex(READ(ESR_EL3));
                console_puts(" spsr=");
                hex(READ(SPSR_EL3));
                console_puts(" cur=");
                hex(READ(CurrentEL));
                console_puts("\n");
            }
            return;
        }
    }
    console_puts("\nMON: sysreg table full esr=");
    hex(esr);
    console_puts("\n");
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
    /* The instruction that trapped, so a system-register access can be named rather than guessed,
     * and the trap controls that would explain a system-register trap at all. */
    console_puts(" elr=");
    hex(level == 3 ? READ(ELR_EL3) : READ(ELR_EL2));
    console_puts(" scr=");
    hex(READ(SCR_EL3));
    console_puts(" cptr3=");
    hex(READ(CPTR_EL3));
    console_puts(" hcr=");
    hex(READ(HCR_EL2));
    console_puts(" cptr2=");
    hex(READ(CPTR_EL2));
    console_puts(" sctlr1=");
    hex(READ(SCTLR_EL1));
    console_puts(" spsr3=");
    hex(READ(SPSR_EL3));
    console_puts("\n");
}
/* The RTC the firmware reads starts at the base the harness pins, so two runs that were told to
 * share a clock really do: -DRTC_BASE carries --rtc-base through to this service. */
#ifndef RTC_BASE
#define RTC_BASE 946684800UL
#endif
/* Seed the live window from the loader's copy, once per machine.
 *
 * The latch alone would not be enough: it lives in RAM the guest can write, so a guest that cleared it
 * could force a re-seed from the loader's copy — which QEMU restores on every reset — and silently
 * discard the flash it had already programmed. The window is therefore also required to look
 * uninitialised: QEMU hands a fresh machine zeroed RAM, and an erased part reads 0xff. A guest that wants the seed to run has to erase the
 * whole live window first, which is a power it already has through the flash service. */
static int nor_window_blank(const unsigned char *window) {
    /* Uniform, not "each byte is 0x00 or 0xff": a window holding a programmed zero among erased
     * bytes is a part with something written on it, and seeding over that would undo the write. */
    unsigned char first = window[0];
    if (first != 0xff && first != 0x00)
        return 0;
    for (unsigned long i = 1; i < NOR_SIZE; i++)
        if (window[i] != first)
            return 0;
    return 1;
}

static void nor_window_seed(void) {
    u64 *state = (u64 *)NOR_STATE_BASE;
    unsigned char *seed = (unsigned char *)NOR_SEED_BASE, *live = (unsigned char *)NOR_LIVE_BASE;
    if (*state == NOR_SEEDED || !nor_window_blank(live))
        return;
    for (unsigned long i = 0; i < NOR_SIZE; i++)
        live[i] = seed[i];
    *state = NOR_SEEDED;
}

static u64 rtc_seconds = RTC_BASE, rtc_counter;
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
/* SMCCC TRNG 1.0 backed by QEMU RNDR and its host CSPRNG. */
static int trng_supported(u32 fid) {
    return fid == 0x84000050 || fid == 0x84000051 || fid == 0x84000052 ||
           fid == 0x84000053 || fid == 0xc4000053;
}
static void trng(u64 *r, u32 fid) {
    u64 request = fid == 0xc4000053 ? r[1] : (u32)r[1];
    for (int i = 0; i < 4; i++)
        r[i] = 0;
    if (!(READ(ID_AA64ISAR0_EL1) >> 60)) {
        r[0] = -1UL;
        return;
    }
    if (fid == 0x84000050) {
        r[0] = 0x10000;
        return;
    }
    if (fid == 0x84000051) {
        r[0] = trng_supported((u32)request) ? 0 : -1UL;
        return;
    }
    if (fid == 0x84000052) {
        /* Independent lab UUID, not a vendor entropy-service identity. */
        r[0] = 0x11f121ff; r[1] = 0x4713da74;
        r[2] = 0xafac5a92; r[3] = 0xe451ab87;
        return;
    }
    unsigned width = fid == 0xc4000053 ? 64 : 32;
    if (!request || request > width * 3) {
        r[0] = -2UL;
        return;
    }
    unsigned part = 0;
    while (request) {
        u64 entropy;
        unsigned available;
        __asm__ volatile("mrs %0, S3_3_C2_C4_0; cset %w1, ne"
                         : "=r"(entropy), "=r"(available) :: "cc");
        if (!available) {
            for (int i = 1; i < 4; i++)
                r[i] = 0;
            r[0] = -3UL;
            return;
        }
        unsigned bits = request > width ? width : request;
        r[3 - part] = bits == 64 ? entropy : entropy & ((1UL << bits) - 1);
        request -= bits;
        part++;
    }
}

void handle(u64 *r, u64 level) {
    u64 esr = level == 3 ? READ(ESR_EL3) : READ(ESR_EL2);
    u32 fid = r[0];
    int ns = level == 3 && (READ(SCR_EL3) & 1);
#ifdef MONITOR_TRACE
    if (esr >> 26 == 0x17 || esr >> 26 == 0x16) {
        console_puts("\nSMC fid=");
        hex(fid);
        console_puts(" ns=");
        hex(ns);
        console_puts(" a1=");
        hex(r[1]);
        console_puts(" a2=");
        hex(r[2]);
        console_puts(" elr=");
        hex(level == 3 ? READ(ELR_EL3) : READ(ELR_EL2));
    }
#endif
    if (fid == 0xc400008a && esr >> 26 == 0x16) {
        u64 n = r[1];
        if (n > 48)
            n = 48;
        for (u64 i = 0; i < n; i++)
            *(volatile u32 *)0x16a00000 = (r[2 + i / 8] >> (8 * (i % 8))) & 255;
        r[0] = 0;
        return;
    }
    if (esr >> 26 == 0x18) {
        /* A system-register access this secure world answers rather than implements: the value a
         * read returns is what the non-secure world last wrote, and zero before that. That is what
         * an unimplemented optional feature looks like from the caller's side, it keeps the boot
         * moving, and every access is named once in the log so the list of registers a faithful
         * model would need is written down rather than guessed. */
        u64 elr = level == 3 ? READ(ELR_EL3) : READ(ELR_EL2);
        sysreg_access(r, esr);
        if (level == 3)
            WRITE(ELR_EL3, elr + 4);
        else
            WRITE(ELR_EL2, elr + 4);
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
    if (fid == 0xc400008d && ns && fan_request(r))
        return;
    if (spbm_request(r))
        return;
    if (trng_supported(fid)) { trng(r, fid); return; }
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
        u64 other_tx = ns ? sp_tx : ns_tx, other_rx = ns ? sp_rx : ns_rx;
        u64 start = ns_memory.descriptor[10], end = start + ns_memory.descriptor[11] * 4096;
        if ((ns && (!ffa_ns_buffer(r[1], 4096) || !ffa_ns_buffer(r[2], 4096))) ||
            r[1] == other_tx || r[1] == other_rx || r[2] == other_tx || r[2] == other_rx ||
            (ns_memory.handle && ((r[1] < end && r[1] + 4096 > start) ||
                                  (r[2] < end && r[2] + 4096 > start)))) {
            ffa_error(r, FFA_DENIED);
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
    if (fid == 0x84000064) {
        u32 feature = r[1];
        int retrieve = feature == 0x84000074 || feature == 0xc4000074;
        if (ns ? (feature != 0x84000073 && feature != 0xc4000073 && feature != 0x84000077)
               : (!retrieve && feature != 0x84000076 && feature != 0x84000073 && feature != 0x84000077))
            unsupported(r);
        else if ((retrieve ? r[2] & ~2UL : r[2]) || r[3] || r[4] || r[5] || r[6] || r[7])
            ffa_error(r, FFA_INVALID_PARAMETERS);
        else {
            success(r);
            if (retrieve)
                r[2] = 2; /* Non-secure attribute supported in retrieve responses. */
        }
        return;
    }
    if ((fid == 0x84000073 || fid == 0xc4000073) && ns) {
        if (!ns_tx || r[1] != 96 || r[2] != 96 || r[3] || r[4] || r[5] || r[6] || r[7]) {
            ffa_error(r, FFA_INVALID_PARAMETERS);
            return;
        }
        u64 mailboxes[] = {ns_tx, ns_rx, sp_tx, sp_rx};
        int error = ffa_memory_share(&ns_memory, (u64 *)ns_tx, mailboxes);
        if (error)
            ffa_error(r, error);
        else {
            success(r);
            r[2] = (u32)ns_memory.handle;
            r[3] = ns_memory.handle >> 32;
        }
        return;
    }
    if ((fid == 0x84000074 || fid == 0xc4000074) && !ns) {
        if (!sp_tx || !sp_rx || r[1] != 64 || r[2] != 64 || r[3] || r[4] || r[5] || r[6] || r[7]) {
            ffa_error(r, FFA_INVALID_PARAMETERS);
            return;
        }
        if (sp_rx_full) {
            ffa_error(r, FFA_BUSY);
            return;
        }
        int error = ffa_memory_retrieve(&ns_memory, (u64 *)sp_tx, (u64 *)sp_rx);
        if (error)
            ffa_error(r, error);
        else {
            sp_rx_full = 1;
            clear(r);
            r[0] = 0x84000075;
            r[1] = r[2] = 96;
        }
        return;
    }
    if (fid == 0x84000076 && !ns) {
        if (!sp_tx || r[1] || r[2] || r[3] || r[4] || r[5] || r[6] || r[7]) {
            ffa_error(r, FFA_INVALID_PARAMETERS);
            return;
        }
        int error = ffa_memory_relinquish(&ns_memory, (u64 *)sp_tx);
        if (error)
            ffa_error(r, error);
        else
            success(r);
        return;
    }
    if (fid == 0x84000077 && ns) {
        if (r[1] >> 32 || r[2] >> 32 || r[3] || r[4] || r[5] || r[6] || r[7]) {
            ffa_error(r, FFA_INVALID_PARAMETERS);
            return;
        }
        int error = ffa_memory_reclaim(&ns_memory, r[1] | r[2] << 32);
        if (error)
            ffa_error(r, error);
        else
            success(r);
        return;
    }
    if (fid == 0x84000073 && !ns) {
        u64 tx = sp_tx;
        u64 *t = (u64 *)tx;
        if (!tx || r[1] != 96 || r[2] != 96 || (u32)t[3] != 16 || (t[3] >> 32) != 1 ||
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
#ifdef MONITOR_TRACE
    /* The firmware's runtime variable code speaks FF-A to an endpoint this monitor does not serve,
     * and guessing at the protocol once crashed the firmware. So the request is written down first:
     * the endpoints, the sizes, and the first bytes of the buffer the caller filled in. */
    if (fid == DIRECT_REQ) {
        console_puts("\nDIRECT_REQ a1=");
        hex(r[1]);
        console_puts(" a2=");
        hex(r[2]);
        console_puts(" a3=");
        hex(r[3]);
        console_puts(" a4=");
        hex(r[4]);
        /* shared_address is only ever set after guest_buffer() accepted it as RAM, so reading it
         * here with translation off cannot fault — the same validation the flash service relies on. */
        if (shared_address && shared_size) {
            console_puts(" buf=");
            unsigned char *b = (unsigned char *)shared_address;
            unsigned long window = shared_size < 48 ? (unsigned long)shared_size : 48UL;
            for (unsigned long i = 0; i < window; i++) {
                hex(b[i]);
                console_puts(" ");
            }
            console_puts("size=");
            hex(shared_size);
        }
    }
#endif
    if (fid == DIRECT_REQ && (r[1] & 0xffff) == 0x8002) {
        unsigned char *b = (unsigned char *)shared_address;
        if (r[1] != 0x80048002 || !shared_address || r[3] != 1 || r[4] != shared_size) {
            unsupported(r);
            return;
        }
        /* Both worlds reach the service; the policy, not the transport,
         * decides dispatch. The board answers a denied request with 0x0A. */
        if (!nor_request((unsigned char *)NOR_LIVE_BASE, NOR_SIZE, b, shared_size, !ns)) {
            unsupported(r);
            return;
        }
        u64 endpoints = r[1];
        clear(r);
        r[0] = DIRECT_RESP;
        r[1] = ((endpoints & 0xffff) << 16) | ((endpoints >> 16) & 0xffff);
        return;
    }
    /* PARTITION_INFO_GET. This model answers with **one** partition — STMM — because that is the one the
     * modelled boot talks to, while a real unit enumerates twenty (research/ffa-partitions-live.json
     * records the measured list, including the fan service at 0x8003). Responding with the measured list
     * would be more faithful only if the descriptor encoding were verified against a guest that parses it;
     * emitting an unverified structure is exactly the kind of guess that has crashed this firmware before,
     * so the model stays deliberately narrow and says so here. */
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
#ifdef SPARK_ACCEPT_MMAP_NOTIFY
    /* Experimental: the firmware's memory-map describer sends non-secure DRAM regions here
     * (0xc4000122). A BL31 on the board would almost certainly accept the notification; this
     * build does, so a boot can be compared against the default NOT_SUPPORTED answer. The
     * notification is not stored anywhere: this monitor enforces no isolation.
     */
    if (fid == 0xc4000122 && ns) {
        success(r);
        return;
    }
#endif
    if ((fid & 0xffff) >= 0x60 && (fid & 0xffff) <= 0x8f)
        unsupported(r);
    else
        r[0] = -1UL;
}
