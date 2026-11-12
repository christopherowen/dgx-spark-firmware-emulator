/* SPDX-License-Identifier: Apache-2.0 */
#ifndef SPARK_FAN_SMC_H
#define SPARK_FAN_SMC_H

/* One SMC round trip; returns the completion id in frame[0]. */
static u64 call(u64 *frame)
{
    /* Load explicitly: named register variables may be spilled before the SMC. */
    __asm__ volatile("ldp x0, x1, [%0]\n"
                     "ldp x2, x3, [%0, #16]\n"
                     "ldr x4, [%0, #32]\n"
                     "smc #0\n"
                     "stp x0, x1, [%0]\n"
                     "str x4, [%0, #32]\n"
                     :
                     : "r"(frame)
                     : "x0", "x1", "x2", "x3", "x4",
                       "x5", "x6", "x7", "x8", "x9", "x10", "x11", "x12", "x13", "x14", "x15",
                       "x16", "x17",   /* cleared by the monitor; also the linker's scratch registers */
                       "memory");
    return frame[0];
}

#endif
