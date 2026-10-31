/* SPDX-License-Identifier: Apache-2.0 */
.global _start
_start:
#ifdef SECURE_PROBE
    ldr x0, =0x93c00000
#else
    ldr x0, =0x88010000
#endif
    mov sp, x0
    bl memory_probe
1:  b 1b
