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
#else
    mov x0, #0xdef
    msr tpidr_el1, x0
    movi v0.2d, #0
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
