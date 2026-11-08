/* SPDX-License-Identifier: Apache-2.0
 * Original probe, assembled with clang --target=aarch64-none-elf and linked at 0x80000000;
 * `tests/test_qemu_profile.py` carries the resulting instruction words so the test needs no
 * toolchain. Regenerate them with:
 *   clang --target=aarch64-none-elf -O2 -ffreestanding -fno-builtin -nostdlib -c <this> -o p.o
 *   ld.lld -Ttext=0x80000000 -e _start p.o -o p.elf && llvm-objcopy -O binary p.elf p.bin
 */
/* Original probe: are all eleven GPIO config windows of the profile present? */
static const unsigned long bases[11] = {
    0x1002d000UL, 0x12440000UL, 0x12460000UL, 0x12830000UL, 0x12850000UL, 0x12870000UL,
    0x12890000UL, 0x128b0000UL, 0x12a50000UL, 0x12e30000UL, 0x13630000UL,
};
void _start(void) {
    volatile unsigned *uart = (volatile unsigned *)0x16a00000UL;
    for (int i = 0; i < 11; i++) {
        volatile unsigned *window = (volatile unsigned *)bases[i];
        (void)window[0];          /* a missing window faults here, before any output */
    }
    *uart = 'P';
    for (;;) { }
}
