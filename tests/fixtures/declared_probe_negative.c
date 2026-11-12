/* Negative twin of the original probe (compares against a value that cannot
 * match, so the failure branch runs). Original probe: the blocks the firmware's ACPI declares must be backed, and the console window
 * must be wider than the eight registers a 16550 models. Prints 'P' or 'F' to the console. */
__attribute__((naked)) void _start(void)
{
    __asm__ volatile(
        /* thermal zone read: 0x1C238818 must hold 3181 (45.0 C in tenths of a kelvin) */
        "movz x1, #0x1c23, lsl #16\n"
        "movk x1, #0x8000\n"
        "ldr  w2, [x1, #0x818]\n"
        "movz w3, #9999\n"
        "cmp  w2, w3\n"
        "b.ne 1f\n"
        /* a vendor 8250 register at +0x200 must be readable and writable scratch */
        "movz x1, #0x16a0, lsl #16\n"
        "movz w2, #0x5a5a\n"
        "str  w2, [x1, #0x200]\n"
        "ldr  w3, [x1, #0x200]\n"
        "cmp  w3, w2\n"
        "b.ne 1f\n"
        /* the _CPC request registers live in their own block at 0x05150000 */
        "movz x1, #0x0515, lsl #16\n"
        "ldr  w2, [x1, #0x480]\n"
        "ldr  w3, [x1, #0x500]\n"
        /* the declared MTEL windows are readable */
        "movz x1, #0x1c87, lsl #16\n"
        "movk x1, #0x0200\n"
        "ldr  w2, [x1]\n"
        "movz x1, #0x311c, lsl #16\n"
        "ldr  w3, [x1]\n"
        /* pass */
        "movz x4, #0x16a0, lsl #16\n"
        "mov  w5, #0x50\n"
        "str  w5, [x4]\n"
        "b    2f\n"
        "1:\n"
        "movz x4, #0x16a0, lsl #16\n"
        "mov  w5, #0x46\n"
        "str  w5, [x4]\n"
        "2:\n"
        "b 2b\n");
}
