/* SPDX-License-Identifier: Apache-2.0
 * Original probe: does the profile expose a second endpoint on bus 1 slot 2, and is the NIC
 * still where it was?
 *
 * ECAM base 0xf300000000, so bus 1 slot 2 function 0 starts at 0x110000 into the window. The
 * probe prints two bytes to the UART: 'S' when a device answers at bus 1 slot 2 and 's' when
 * nothing does, then 'N' when the NIC still answers at slot 0 and 'n' when it does not. The second
 * byte is what keeps this change honest: adding storage must not disturb the NIC's mapping.
 */
#define ECAM 0xf300000000UL
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
    volatile unsigned *uart = (volatile unsigned *)UART;
    unsigned storage = ecam[0x110000 / 4];
    unsigned network = ecam[0x100000 / 4];
    unsigned nic_present = (network & 0xffff) != 0xffff && network != 0;
    unsigned storage_present = storage != 0xffffffff && storage != 0;
    *uart = storage_present ? 'S' : 's';
    *uart = nic_present ? 'N' : 'n';
    for (;;)
        __asm__ volatile("wfi");
}
