/* SPDX-License-Identifier: Apache-2.0
 * Original AArch64 EFI application, delivered only through PXE/TFTP.
 * A fresh build token distinguishes execution from stale output or downloads.
 */
typedef unsigned long long UINTN;
typedef unsigned short CHAR16;
typedef struct {
    void *Reset;
    UINTN (*OutputString)(void *, const CHAR16 *);
} TEXT_OUTPUT;
typedef struct {
    unsigned char HeaderAndInput[64];
    TEXT_OUTPUT *ConOut;
} SYSTEM_TABLE_PREFIX;

#ifndef PROBE_TOKEN
#error PROBE_TOKEN must be supplied by the local probe builder
#endif

UINTN EfiMain(void *image, SYSTEM_TABLE_PREFIX *system)
{
    static const CHAR16 message[] = L"\r\nSPARK_PXE_EXECUTED:" PROBE_TOKEN L"\r\n";
    (void)image;
    system->ConOut->OutputString(system->ConOut, message);
    for (;;) {
        __asm__ volatile("wfi");
    }
}
