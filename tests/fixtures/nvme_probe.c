/* SPDX-License-Identifier: Apache-2.0
 * Original probe: does each endpoint answer in exactly one place, and nothing else answer at all?
 *
 * Two configuration windows exist. 0xf300000000 is segment 0, which the firmware's own MCFG entry
 * gives it; 0x29000000 is segment 15 — the vendor's SSDT enables that bridge as `PCIF` and none of the
 * other fifteen, so it is the only root a Linux guest walks. Offsets: bus 1 slot 0 starts at 0x100000
 * and bus 1 slot 2 at 0x110000 in either window; on the root bus, slot 2 starts at 0x10000 and slot 3
 * at 0x18000.
 *
 * Two rules are being checked, both of them learned by measurement. A device reachable in both windows
 * is enumerated twice and the firmware then never reaches PXE; and a bridge in segment 15 derails the
 * same boot even with nothing behind it, so storage sits on that segment's root bus and no root port is
 * modelled at all.
 *
 * The probe prints five bytes to the UART:
 *   'N'/'n'  the NIC at segment 0 bus 1 slot 0
 *   'S'/'s'  a controller at segment 15 root slot 2
 *   'D'/'d'  a second controller at segment 15 root slot 3
 *   'U'/'u'  segment 0 slot 2 is empty — the same controller does not answer in both windows
 *   'B'/'b'  segment 15 slot 0 is empty — no root port is modelled
 */
#define ECAM 0xf300000000UL
#define PCIF 0x29000000UL
#define UART 0x16a00000UL
#define STACK_TOP 0x84000000UL

void start_c(void);

__attribute__((naked)) void _start(void)
{
    __asm__ volatile("mov x9, %0\n"
                     "mov sp, x9\n"
                     "b start_c" : : "i"(STACK_TOP) : "x9");
}

void start_c(void)
{
    volatile unsigned *ecam = (volatile unsigned *)ECAM;
    volatile unsigned *pcif = (volatile unsigned *)PCIF;
    volatile unsigned *uart = (volatile unsigned *)UART;
    unsigned network = ecam[0x100000 / 4];
    unsigned dup_storage = ecam[0x110000 / 4];
    unsigned storage = pcif[0x010000 / 4];
    unsigned second = pcif[0x018000 / 4];
    unsigned bridge = pcif[0x000000 / 4];
    unsigned nic_present = (network & 0xffff) != 0xffff && network != 0;
    unsigned storage_present = storage != 0xffffffff && storage != 0;
    unsigned second_present = second != 0xffffffff && second != 0;
    unsigned storage_unique = dup_storage == 0xffffffff;
    unsigned bridge_absent = bridge == 0xffffffff;
    *uart = nic_present ? 'N' : 'n';
    *uart = storage_present ? 'S' : 's';
    *uart = second_present ? 'D' : 'd';
    *uart = storage_unique ? 'U' : 'u';
    *uart = bridge_absent ? 'B' : 'b';
    for (;;)
        __asm__ volatile("wfi");
}
