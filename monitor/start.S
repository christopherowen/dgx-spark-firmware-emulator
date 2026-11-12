/* SPDX-License-Identifier: Apache-2.0
 * Original experimental Spark firmware laboratory monitor.
 */
.section .text.boot, "ax"
.global _start
_start:
    /* A secondary must not clear BSS or program the GIC under the primary's feet, so it parks
     * before touching anything shared. `secondary_release` and `secondary_entry` are in .data: the
     * primary's PSCI CPU_ON fills them and sends an event, and the parked core picks the work up.
     * A core that arrives here before any release waits; nothing about the one-CPU path changes,
     * because with -smp 1 this core is the only one. */
    mrs x0, mpidr_el1
    and x0, x0, #0xff
    cbnz x0, secondary_park
    ldr x0, =__stack_top
    mov sp, x0
    ldr x0, =vectors
    msr vbar_el3, x0
    msr cptr_el3, xzr
    mov x0, #0x30
    msr cpacr_el1, x0
    // Disable GIC security to match the existing one-CPU firmware profile.
    mov x0, #0x06800000
    mov w1, #0x40
    str w1, [x0]
    ldr x0, =__bss_start
    ldr x1, =__bss_end
1:  cmp x0, x1
    b.hs 2f
    str xzr, [x0], #8
    b 1b
2:  bl boot
    b .

secondary_park:
    ldr x1, =secondary_release
1:  ldr x2, [x1]
    cbz x2, 2f
    ldr x0, =secondary_context
    ldr x0, [x0]
    ldr x3, =secondary_entry
    ldr x3, [x3]
    mov x4, #0
    str x4, [x1]                 /* one release per request */
    /* Enter the caller's entry point the way a secondary is entered: EL2h, NS set, context in x0. */
    msr elr_el3, x3
    mov x4, #0x3c9
    msr spsr_el3, x4
    mrs x4, scr_el3
    orr x4, x4, #1
    msr scr_el3, x4
    eret
2:  wfe
    b 1b

.section .data, "aw"
.global secondary_release, secondary_entry, secondary_context
.balign 8
/* Filled by PSCI CPU_ON and read by a parked core. .data, not .bss: a secondary must not have to
 * wait for the primary's BSS clear to see them, and they must survive the clear. */
secondary_release: .quad 0
secondary_entry:   .quad 0
secondary_context: .quad 0

.section .text.vectors, "ax"
.balign 2048
vectors:
.rept 16
    b exception_entry
    .balign 128
.endr
exception_entry:
    sub sp, sp, #256
    stp x0, x1, [sp]
    stp x2, x3, [sp, #16]
    stp x4, x5, [sp, #32]
    stp x6, x7, [sp, #48]
    stp x8, x9, [sp, #64]
    stp x10, x11, [sp, #80]
    stp x12, x13, [sp, #96]
    stp x14, x15, [sp, #112]
    stp x16, x17, [sp, #128]
    stp x18, x19, [sp, #144]
    stp x20, x21, [sp, #160]
    stp x22, x23, [sp, #176]
    stp x24, x25, [sp, #192]
    stp x26, x27, [sp, #208]
    stp x28, x29, [sp, #224]
    str x30, [sp, #240]
    mov x0, sp
    mrs x1, CurrentEL
    lsr x1, x1, #2
    bl handle
    ldp x0, x1, [sp]
    ldp x2, x3, [sp, #16]
    ldp x4, x5, [sp, #32]
    ldp x6, x7, [sp, #48]
    ldp x8, x9, [sp, #64]
    ldp x10, x11, [sp, #80]
    ldp x12, x13, [sp, #96]
    ldp x14, x15, [sp, #112]
    ldp x16, x17, [sp, #128]
    ldp x18, x19, [sp, #144]
    ldp x20, x21, [sp, #160]
    ldp x22, x23, [sp, #176]
    ldp x24, x25, [sp, #192]
    ldp x26, x27, [sp, #208]
    ldp x28, x29, [sp, #224]
    ldr x30, [sp, #240]
    add sp, sp, #256
    eret
.global enter
enter:
    mov x0, x1
    mov x1, xzr
    mov x2, xzr
    mov x3, xzr
    eret

.global save_fp
save_fp:
    stp q0, q1, [x0, #0]
    stp q2, q3, [x0, #32]
    stp q4, q5, [x0, #64]
    stp q6, q7, [x0, #96]
    stp q8, q9, [x0, #128]
    stp q10, q11, [x0, #160]
    stp q12, q13, [x0, #192]
    stp q14, q15, [x0, #224]
    stp q16, q17, [x0, #256]
    stp q18, q19, [x0, #288]
    stp q20, q21, [x0, #320]
    stp q22, q23, [x0, #352]
    stp q24, q25, [x0, #384]
    stp q26, q27, [x0, #416]
    stp q28, q29, [x0, #448]
    stp q30, q31, [x0, #480]
    mrs x1, fpcr
    mrs x2, fpsr
    str x1, [x0, #512]
    str x2, [x0, #520]
    ret
.global restore_fp
restore_fp:
    ldp q0, q1, [x0, #0]
    ldp q2, q3, [x0, #32]
    ldp q4, q5, [x0, #64]
    ldp q6, q7, [x0, #96]
    ldp q8, q9, [x0, #128]
    ldp q10, q11, [x0, #160]
    ldp q12, q13, [x0, #192]
    ldp q14, q15, [x0, #224]
    ldp q16, q17, [x0, #256]
    ldp q18, q19, [x0, #288]
    ldp q20, q21, [x0, #320]
    ldp q22, q23, [x0, #352]
    ldp q24, q25, [x0, #384]
    ldp q26, q27, [x0, #416]
    ldp q28, q29, [x0, #448]
    ldp q30, q31, [x0, #480]
    ldr x1, [x0, #512]
    ldr x2, [x0, #520]
    msr fpcr, x1
    msr fpsr, x2
    ret
