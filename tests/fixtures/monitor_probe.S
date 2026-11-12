/* SPDX-License-Identifier: Apache-2.0
 * Original synthetic guests. No vendor firmware is used by this test.
 */
.global _start
_start:
#ifdef SECURE_PROBE
    mov x0, #0x300000
    msr cpacr_el1, x0
    mov x0, #0xabc
    msr tpidr_el1, x0
    /* Legacy executable pages: a normal-world PIE leak must not reinterpret them. */
    adr x0, secure_table
    msr ttbr0_el1, x0
    mov x0, #0xff
    msr mair_el1, x0
    ldr x0, =0x800019             /* EPD1, 39-bit TTBR0 VA, 4 KiB granule */
    msr tcr_el1, x0
    isb
    mrs x0, sctlr_el1
    orr x0, x0, #1
    msr sctlr_el1, x0
    isb
    movi v0.2d, #0
    ldr x0, =0x8400006b
    hvc #0
secure_request:
    ldr x0, =0xc4000070
    ldr x1, =0x80040000
    add x3, x3, #1
    mrs x4, tpidr_el1
    movi v0.2d, #0xff
    hvc #0
    b secure_request
    .balign 4096
secure_table:
    .quad 0, 0, secure_l2 + 3  /* L1[2]: the 0x80000000 VA range. */
    .space 4096 - 24
secure_l2:
    .space 157 * 8
    .quad secure_l3 + 3        /* L2[157]: 0x93a00000..0x93bfffff. */
    .space 4096 - 158 * 8
secure_l3:
    .set secure_page, 0x93a00703
    .rept 512
    .quad secure_page           /* 4 KiB identity pages, AF=1, legacy executable permissions. */
    .set secure_page, secure_page + 4096
    .endr
#else
    mov x0, #0xdef
    msr tpidr_el1, x0
    movi v0.2d, #0
    /* Network DEPEX needs real SMCCC TRNG. Check discovery and bounds. */
    ldr x0, =0x84000050
    smc #0
    cmp x0, #0x10, lsl #12
    b.ne fail
    ldr x0, =0x84000051
    ldr x1, =0xc4000053
    smc #0
    cbnz x0, fail
    ldr x0, =0x84000051
    mov x1, #0x1234
    smc #0
    cmn x0, #1
    b.ne fail
    ldr x0, =0xc4000053
    mov x1, #193
    smc #0
    cmn x0, #2
    b.ne fail
    orr x5, x1, x2
    orr x5, x5, x3
    cbnz x5, fail
    ldr x0, =0x84000053
    mov x1, #0
    smc #0
    cmn x0, #2
    b.ne fail
    ldr x0, =0x84000053
    mov x1, #97
    smc #0
    cmn x0, #2
    b.ne fail
    /* 33 bits: x3 low 32, x2 low 1, x1 unused; no garbage upper bits. */
    ldr x0, =0x84000053
    mov x1, #33
    smc #0
    cbnz x0, fail
    cbnz x1, fail
    lsr x5, x2, #1
    cbnz x5, fail
    lsr x5, x3, #32
    cbnz x5, fail
    /* Full-width outputs must not repeat across independent requests. */
    ldr x0, =0xc4000053
    mov x1, #192
    smc #0
    cbnz x0, fail
    mov x20, x1
    mov x21, x2
    mov x22, x3
    ldr x0, =0xc4000053
    mov x1, #192
    smc #0
    cbnz x0, fail
    eor x1, x1, x20
    eor x2, x2, x21
    eor x3, x3, x22
    orr x1, x1, x2
    orr x1, x1, x3
    cbz x1, fail
    /* An overflowing map must fail before dereferencing either pointer. */
    ldr x0, =0xc4000066
    mov x1, #-4096
    ldr x2, =0x88001000
    mov x3, #1
    smc #0
    ldr x5, =0x84000060
    cmp x0, x5
    b.ne fail
    ldr x0, =0xc4000066
    ldr x1, =0x88000000
    ldr x2, =0x88001000
    mov x3, #1
    smc #0
    ldr x5, =0x84000061
    cmp x0, x5
    b.ne fail
    /* Discover the MM endpoint through the mapped RX page. */
    ldr x0, =0x84000068
    mov x1, xzr
    mov x2, xzr
    mov x3, xzr
    mov x4, xzr
    mov x5, xzr
    smc #0
    ldr x5, =0x84000061
    cmp x0, x5
    b.ne fail
    cmp x2, #1
    b.ne fail
    ldr x5, =0x88001000
    ldr w5, [x5]
    ldr w6, =0x00018004
    cmp w5, w6
    b.ne fail
    ldr x0, =0x84000065
    smc #0
    ldr x5, =0x84000061
    cmp x0, x5
    b.ne fail
    /* PSCI: with two CPUs modelled, CPU_ON must really start the parked core and AFFINITY_INFO must
     * agree afterwards; a one-CPU build refuses both. */
#ifdef TWO_CPUS
    ldr x0, =0xc4000004
    mov x1, #1
    mov x2, #0
    smc #0
    cmp w0, #1              /* OFF until started */
    b.ne fail
    ldr x0, =0xc4000003
    mov x1, #1
    adr x2, secondary_idle
    mov x3, #0
    smc #0
    cbnz w0, fail
    ldr x0, =0xc4000004
    mov x1, #1
    mov x2, #0
    smc #0
    cbnz w0, fail           /* ON now */
    b psci_basics
secondary_idle:
    wfe
    b secondary_idle
psci_basics:
#endif
    /* PSCI: the caller's own CPU must read as on and an affinity this model does not have must be
     * refused. */
    ldr x0, =0xc4000004
    mrs x1, mpidr_el1
    and x1, x1, #0xff      /* the affinity value at level 0, without MPIDR's RES1 bit */
    mov x2, #0
    smc #0
    cbnz x0, fail
    ldr x0, =0xc4000004
    mov x1, #7
    mov x2, #0
    smc #0
    cmn w0, #2             /* INVALID_PARAMETERS, zero-extended from 32 bits */
    b.ne fail
    ldr x0, =0x84000004
    mov x1, #0
    mov x2, #1
    smc #0
    cbnz x0, fail
#ifndef TWO_CPUS
    /* With one CPU modelled there is no parked core to start, so the answer is a parameter error
     * rather than a promise. */
    ldr x0, =0xc4000003
    mov x1, #1
    mov x2, #0
    mov x3, #0
    smc #0
    cmn w0, #2             /* INVALID_PARAMETERS, zero-extended from 32 bits */
    b.ne fail
#endif
    ldr x0, =0x8400000a
    ldr x1, =0xc4000004
    smc #0
    cmp x0, #3
    b.ne fail
    /* Model the installed kernel enabling stage-1 permission indirection in both regimes.
     * Index zero denies access, so leaking PIE into the secure legacy mapping faults on return. */
    mov x0, #0x4000
    msr S3_4_C1_C2_2, x0        /* HCRX_EL2.TCR2En makes the EL1 control effective. */
    mov x0, #2
    msr S3_0_C2_C0_3, x0        /* TCR2_EL1.PIE */
    msr S3_4_C2_C0_3, x0        /* TCR2_EL2.PIE */
    mov x0, #0x1230
    msr S3_0_C10_C2_3, x0       /* PIR_EL1 */
    mov x0, #0x4560
    msr S3_4_C10_C2_3, x0       /* PIR_EL2 */
    mov x0, #0x7890
    msr S3_0_C10_C2_2, x0       /* PIRE0_EL1 */
    mov x0, #0xabc0
    msr S3_4_C10_C2_2, x0       /* PIRE0_EL2 */
    isb
    /* Two real EL3/S-EL2/NS-EL2 world switches must preserve FP and TLS. */
    mov x19, #2
request:
    ldr x0, =0xc400006f
    mov x1, #0x8004
    mov x2, xzr
    mov x3, #11
    smc #0
    ldr x5, =0xc4000070
    cmp x0, x5
    b.ne fail
    cmp x3, #12
    b.ne fail
    cmp x4, #0xabc
    b.ne fail
    mrs x5, tpidr_el1
    cmp x5, #0xdef
    b.ne fail
    umov x5, v0.d[0]
    cbnz x5, fail
    mrs x5, S3_0_C2_C0_3
    cmp x5, #2
    b.ne fail
    mrs x5, S3_4_C2_C0_3
    cmp x5, #2
    b.ne fail
    mrs x5, S3_0_C10_C2_3
    mov x6, #0x1230
    cmp x5, x6
    b.ne fail
    mrs x5, S3_4_C10_C2_3
    mov x6, #0x4560
    cmp x5, x6
    b.ne fail
    mrs x5, S3_0_C10_C2_2
    mov x6, #0x7890
    cmp x5, x6
    b.ne fail
    mrs x5, S3_4_C10_C2_2
    mov x6, #0xabc0
    cmp x5, x6
    b.ne fail
    mrs x5, S3_4_C1_C2_2
    cmp x5, #4, lsl #12
    b.ne fail
    subs x19, x19, #1
    b.ne request
    /* A timer output must reach PPI 30 in the enlarged GIC. */
    mov x0, #1
    msr cntp_tval_el0, x0
    msr cntp_ctl_el0, x0
    isb
    ldr x1, =0x06890200
    mov x2, #0xffff
poll:
    ldr w0, [x1]
    tbnz w0, #30, passed
    subs x2, x2, #1
    b.ne poll
    b fail
passed:
    adr x1, pass_message
    b print
fail:
    adr x1, fail_message
print:
    ldr x2, =0x16a00000
1:  ldrb w0, [x1], #1
    cbz w0, 2f
    str w0, [x2]
    b 1b
2:  b 2b
pass_message: .asciz "MONITOR PASS\n"
fail_message: .asciz "MONITOR FAIL\n"
#endif
