/* SPDX-License-Identifier: Apache-2.0
 * Original probe, assembled with clang --target=aarch64-none-elf and linked at 0x80000000;
 * `tests/test_qemu_profile.py` carries the resulting instruction words so the test needs no
 * toolchain. Regenerate them with:
 *   clang --target=aarch64-none-elf -O2 -ffreestanding -fno-builtin -nostdlib -c <this> -o p.o
 *   ld.lld -Ttext=0x80000000 -e _start p.o -o p.elf && llvm-objcopy -O binary p.elf p.bin
 */
/* Original probe: is the PCIe ECAM window mapped at the board's base? */
void _start(void) {
    volatile unsigned *ecam = (volatile unsigned *)0xf300000000UL;
    volatile unsigned *uart = (volatile unsigned *)0x16a00000UL;
    unsigned id = ecam[0];                  /* vendor/device of 0000:00:00.0 */
    unsigned vendor = id & 0xffff;
    *uart = (vendor != 0xffff) ? 'P' : 'N'; /* N = readable but no device present */
    for (;;) { }
}
