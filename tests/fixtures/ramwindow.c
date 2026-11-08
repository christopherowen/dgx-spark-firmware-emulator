/* SPDX-License-Identifier: Apache-2.0
 * Original probe, assembled with clang --target=aarch64-none-elf and linked at 0x80000000;
 * `tests/test_qemu_profile.py` carries the resulting instruction words so the test needs no
 * toolchain. Regenerate them with:
 *   clang --target=aarch64-none-elf -O2 -ffreestanding -fno-builtin -nostdlib -c <this> -o p.o
 *   ld.lld -Ttext=0x80000000 -e _start p.o -o p.elf && llvm-objcopy -O binary p.elf p.bin
 */
/* Original probe: pins the profile's DRAM window.
 *   - 0xBFFFFFF8 must be RAM at -m 2G (the top of the usable gigabyte);
 *   - 0x40010000 must be PCI MMIO, not RAM (the profile aliases BAR space there).
 * P only when both hold. */
void _start(void) {
    volatile unsigned *uart = (volatile unsigned *)0x16a00000UL;
    volatile unsigned *top = (volatile unsigned *)0xBFFFFFF8UL;
    volatile unsigned *alias = (volatile unsigned *)0x40010000UL;
    top[0] = 0x13572468U;
    int ram_ok = (top[0] == 0x13572468U);
    alias[0] = 0x24681357U;
    int alias_is_mmio = (alias[0] != 0x24681357U);
    *uart = (ram_ok && alias_is_mmio) ? 'P' : 'F';
    for (;;) { }
}
