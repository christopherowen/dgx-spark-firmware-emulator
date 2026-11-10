/* SPDX-License-Identifier: Apache-2.0
 * A host harness for the probe's result channel: call the real header, watch what it stores.
 *
 * `tests/fixtures/efi_var.h` runs on hardware where a wrong table offset is a crash, and until now
 * only its spelling was checked. This compiles the header for the host and gives it a synthetic
 * system table: a canary function sits at the RuntimeServices slot's +0x50, the real recording
 * function at +0x58. If the header read the wrong offset the harness would call the canary and print
 * CANARY, and the test fails on that line rather than on a crash.
 *
 * Output (one line):
 *   name=<utf16 hex> guid=<d1>-<d2>-<d3>-<b4..b9 hex>-<b10..b15 hex> attributes=<hex> size=<n> data=<hex>
 */
#include <stdio.h>
#include <string.h>

#include "efi_var.h"

static unsigned char table[0x100];
static int canary_called;

static u64 canary(char16 *name, efi_guid *guid, unsigned int attributes, u64 size, void *data)
{
    (void)name; (void)guid; (void)attributes; (void)size; (void)data;
    canary_called = 1;
    return 0;
}

static char16 seen_name[64];
static efi_guid seen_guid;
static unsigned int seen_attributes;
static u64 seen_size;
static unsigned char seen_data[REPORT_MAX];

static u64 recorded(char16 *name, efi_guid *guid, unsigned int attributes, u64 size, void *data)
{
    unsigned i = 0;
    while (name[i] && i < 63) {
        seen_name[i] = name[i];
        i++;
    }
    seen_name[i] = 0;
    seen_guid = *guid;
    seen_attributes = attributes;
    seen_size = size;
    memcpy(seen_data, data, size < REPORT_MAX ? (size_t)size : REPORT_MAX);
    return 0;
}

int main(void)
{
    /* System table: the runtime-services pointer lives at 0x58 (verified against the firmware). */
    unsigned char system_table[0x80];
    memset(system_table, 0, sizeof system_table);
    memset(table, 0, sizeof table);
    *(void **)(table + 0x50) = (void *)canary;
    *(void **)(table + 0x58) = (void *)recorded;
    *(void **)(system_table + 0x58) = (void *)table;

    if (!report_init(system_table)) {
        printf("INIT-FAILED\n");
        return 1;
    }
    report_text("R0 1978");
    report_char('\n');
    report_number(4096);
    report_char('\n');
    report_hex(0xdeadbeefULL);
    report_char('\n');
    if (report_flush() != 0) {
        printf("FLUSH-FAILED\n");
        return 2;
    }
    if (canary_called) {
        printf("CANARY\n");
        return 3;
    }

    printf("name=");
    for (unsigned i = 0; seen_name[i]; i++)
        printf("%04x", (unsigned)seen_name[i]);
    printf(" guid=%08x-%04x-%04x-", seen_guid.data1, seen_guid.data2, seen_guid.data3);
    for (int i = 0; i < 8; i++)
        printf("%02x", seen_guid.data4[i]);
    printf(" attributes=%u size=%llu data=", seen_attributes, (unsigned long long)seen_size);
    for (u64 i = 0; i < seen_size; i++)
        putchar(seen_data[i]);
    printf("\n");
    return 0;
}
