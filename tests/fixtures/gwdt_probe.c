/* SPDX-License-Identifier: Apache-2.0
 * Original probe, assembled with clang --target=aarch64-none-elf and linked at 0x80000000;
 * `tests/test_qemu_profile.py` carries the resulting instruction words so the test needs no
 * toolchain. Regenerate them with:
 *   clang --target=aarch64-none-elf -O2 -ffreestanding -fno-builtin -nostdlib -c <this> -o p.o
 *   ld.lld -Ttext=0x80000000 -e _start p.o -o p.elf && llvm-objcopy -O binary p.elf p.bin
 */
/* Original probe: is the SBSA GWDT at the board's addresses and writable? */
void _start(void) {
    volatile unsigned *ctrl = (volatile unsigned *)0x1c042000UL;
    volatile unsigned *uart = (volatile unsigned *)0x16a00000UL;
    ctrl[2] = 0x3b9aca00U;          /* WOR: 1e9 ticks, about a second at 1 GHz */
    unsigned wor = ctrl[2];
    unsigned wcs = ctrl[0];
    int ok = (wor == 0x3b9aca00U) && (wcs == 0U);
    *uart = ok ? 'P' : 'F';
    for (;;) { }
}
