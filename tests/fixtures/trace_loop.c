/* SPDX-License-Identifier: Apache-2.0
 * A guest that executes one instruction a known number of times, then says so.
 *
 * Entry is the first thing in the image: a freestanding object emits its functions in source
 * order, and the machine starts at the image base. `_start` is naked and sets a stack before
 * calling anything, because the compiler's own prologue would otherwise store through the reset
 * value of sp. The UART byte afterwards is the exit marker: a test can then tell "the probe
 * finished" from "the trace stopped".
 */
#define UART 0x16a00000UL
#define ITERATIONS 3200
#define STACK_TOP 0x84000000UL

void start_c(void);

__attribute__((naked)) void _start(void)
{
    __asm__ volatile("mov x9, %0\n"
                     "mov sp, x9\n"
                     "b start_c" : : "i"(STACK_TOP) : "x9");
}

static void put(char c)
{
    *(volatile unsigned *)UART = c;
}

__attribute__((noinline)) static void loop(unsigned count)
{
    /* The counter lives in memory and the function is not inlined, so the decrement is a real
     * instruction a test can find and the plugin can watch. */
    while (*(volatile unsigned *)&count)
        *(volatile unsigned *)&count -= 1;
}

void start_c(void)
{
    loop(ITERATIONS);
    put('P');
    for (;;)
        __asm__ volatile("wfi");
}
