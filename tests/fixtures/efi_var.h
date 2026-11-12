/* SPDX-License-Identifier: Apache-2.0
 * A result channel for a probe that runs where nobody can read the screen.
 *
 * A probe delivered as a boot option on a headless unit prints to ConOut, which is a display nobody is
 * sitting in front of. The result has to survive the boot. UEFI gives one durable place a payload can
 * write without a file system: a non-volatile variable, which Linux then exposes as
 * /sys/firmware/efi/efivars/<Name>-<Guid>.
 *
 * The variable name and GUID are fixed so the read side is one path. Attributes are the standard
 * NON_VOLATILE | BOOTSERVICE_ACCESS | RUNTIME_ACCESS. Nothing here touches the vendor flash operations
 * that the brick came from; a variable write is the same call an OS installer makes on every boot.
 *
 * Compile with -DREPORT_VAR to include it. Without the define, no report code is linked and the probe is
 * byte-for-byte the one the rehearsal tests build.
 */
#ifndef EFI_VAR_H
#define EFI_VAR_H

typedef unsigned short char16;
typedef unsigned long long u64;

typedef struct {
    unsigned int data1;
    unsigned short data2;
    unsigned short data3;
    unsigned char data4[8];
} efi_guid;

/* /sys/firmware/efi/efivars/SparkProbe-53504152-4b50-524f-4245-010000000001 */
#define REPORT_NAME "SparkProbe"
#define REPORT_GUID_DATA1 0x53504152u
#define REPORT_GUID_DATA2 0x4b50u
#define REPORT_GUID_DATA3 0x524fu
#define REPORT_GUID_BYTES { 0x42, 0x45, 0x01, 0x00, 0x00, 0x00, 0x00, 0x01 }

#define REPORT_MAX 512
#define EFI_VARIABLE_NON_VOLATILE 0x00000001u
#define EFI_VARIABLE_BOOTSERVICE_ACCESS 0x00000002u
#define EFI_VARIABLE_RUNTIME_ACCESS 0x00000004u

static char16 report_name[] = {
    'S', 'p', 'a', 'r', 'k', 'P', 'r', 'o', 'b', 'e', 0
};
static efi_guid report_guid = {
    REPORT_GUID_DATA1, REPORT_GUID_DATA2, REPORT_GUID_DATA3, REPORT_GUID_BYTES
};

static unsigned char report_data[REPORT_MAX];
static unsigned int report_len;
static void *report_runtime;

/* The runtime table is at system_table + 0x58 (verified against the firmware's own table in
 * analysis/EFI-BOOT-SERVICES-CALLS-FROM-OUR-PAYLOADS-DO-NOT-BEHAVE). SetVariable is its ninth
 * function pointer: 24-byte table header, then GetTime .. GetNextVariableName, so 0x58. */
static inline int report_init(void *system_table)
{
    report_runtime = *(void **)((unsigned char *)system_table + 0x58);
    return report_runtime != 0;
}

static inline void report_char(char c)
{
    if (report_len < REPORT_MAX)
        report_data[report_len++] = (unsigned char)c;
}

static inline void report_text(const char *text)
{
    while (*text)
        report_char(*text++);
}

static inline void report_number(u64 value)
{
    char digits[24];
    int n = 0;
    if (!value)
        digits[n++] = '0';
    while (value) {
        digits[n++] = (char)('0' + (value % 10));
        value /= 10;
    }
    while (n)
        report_char(digits[--n]);
}

static inline void report_hex(u64 value)
{
    static const char hex[] = "0123456789abcdef";
    report_char('0');
    report_char('x');
    for (int shift = 60; shift >= 0; shift -= 4)
        report_char(hex[(value >> shift) & 0xf]);
}

/* Returns 0 when the firmware stored the variable; the caller still prints to ConOut either way. */
static inline u64 report_flush(void)
{
    typedef u64 (*set_variable_fn)(char16 *, efi_guid *, unsigned int, u64, void *);
    if (!report_runtime || !report_len)
        return 1;
    set_variable_fn set_variable = *(set_variable_fn *)((unsigned char *)report_runtime + 0x58);
    return set_variable(report_name, &report_guid,
                        EFI_VARIABLE_NON_VOLATILE | EFI_VARIABLE_BOOTSERVICE_ACCESS |
                            EFI_VARIABLE_RUNTIME_ACCESS,
                        report_len, report_data);
}

#endif
