/* SPDX-License-Identifier: Apache-2.0
 * Original experimental Spark firmware laboratory monitor.
 */
ENTRY(_start)
SECTIONS {
 . = 0x80020000;
 .text : { *(.text.boot) *(.text.vectors) *(.text*) }
 .rodata ALIGN(16) : { *(.rodata*) }
 .data : { *(.data*) }
 .bss (NOLOAD) : { __bss_start = .; *(.bss*) *(COMMON) . = ALIGN(16); __bss_end = .; }
 . = ALIGN(16);
 __stack_top = 0x800f0000;
 ASSERT(__bss_end < 0x800d0000, "monitor overlaps reserved world stacks")
 /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) }
}
