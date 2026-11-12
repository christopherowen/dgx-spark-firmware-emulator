/* Original probe: the block \\_SB.MTEL declares must be readable and writable memory, at its base
 * and at the far end where the processor _CPC registers live. Prints 'P' or 'F' to the 16550. */
__attribute__((naked)) void _start(void)
{
    __asm__ volatile(
        "movz x1, #0x0517, lsl #16\n"      /* 0x05170000, MTEL's base */
        "movz w2, #0x3344\n"
        "movk w2, #0x1122, lsl #16\n"
        "str  w2, [x1, #0x100]\n"          /* the OS PL1 slot */
        "ldr  w3, [x1, #0x100]\n"
        "cmp  w3, w2\n"
        "b.ne 1f\n"
        "movz x6, #0xc404\n"               /* 0x5C404: CPUEB's CPPC_INTERFACE offset */
        "movk x6, #0x0005, lsl #16\n"
        "add  x6, x1, x6\n"
        "str  w2, [x6]\n"                  /* the first _CPC register */
        "ldr  w3, [x6]\n"
        "cmp  w3, w2\n"
        "b.ne 1f\n"
        "movz x4, #0x16a0, lsl #16\n"
        "mov  w5, #0x50\n"                 /* 'P' */
        "str  w5, [x4]\n"
        "b    2f\n"
        "1:\n"
        "movz x4, #0x16a0, lsl #16\n"
        "mov  w5, #0x46\n"                 /* 'F' */
        "str  w5, [x4]\n"
        "2:\n"
        "b 2b\n");
}
