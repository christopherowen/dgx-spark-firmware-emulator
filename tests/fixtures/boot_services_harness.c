/* SPDX-License-Identifier: Apache-2.0
 * Execute the unmodified EFI entry point against synthetic tables and protocols.
 */
#include <stdio.h>
#include <string.h>

#define _start probe_start
#define putc probe_putc
#define puts probe_puts
#ifdef DEVICE_PATH
#include "dp_self.c"
#else
#include "fs_census.c"
#endif
#undef puts
#undef putc
#undef _start

static unsigned opens, locates, releases, unexpected;
static char output[4096];
static unsigned output_length;
static int image_handle, path_handle, fs_handle;
static void *handles[] = { &path_handle, &fs_handle };
static unsigned char end_path[] = { 0x7f, 0xff, 4, 0 };
static unsigned char device_path[] = { 1, 1, 4, 0, 0x7f, 0xff, 4, 0 };
static void *loaded_image[5];
static const unsigned char loaded_guid[] = {
    0xa1, 0x31, 0x1b, 0x5b, 0x62, 0x95, 0xd2, 0x11,
    0x8e, 0x3f, 0, 0xa0, 0xc9, 0x69, 0x72, 0x3b
};
static const unsigned char device_guid[] = {
    0x91, 0x6e, 0x57, 9, 0x3f, 0x6d, 0xd2, 0x11,
    0x8e, 0x39, 0, 0xa0, 0xc9, 0x69, 0x72, 0x3b
};
static const unsigned char filesystem_guid[] = {
    0x22, 0x5b, 0x4e, 0x96, 0x59, 0x64, 0xd2, 0x11,
    0x8e, 0x39, 0, 0xa0, 0xc9, 0x69, 0x72, 0x3b
};

static u64 capture(void *console, const char16 *text)
{
    (void)console;
    while (*text && output_length < sizeof(output)) output[output_length++] = (char)*text++;
    return 0;
}

static u64 canary(void)
{
    unexpected++;
    return 1;
}

static u64 open_protocol(void *handle, const unsigned char *guid, void **result)
{
    opens++;
    *result = 0;
    if (handle == &image_handle && !memcmp(guid, loaded_guid, 16)) *result = loaded_image;
    else if ((handle == &path_handle || handle == &fs_handle) && !memcmp(guid, device_guid, 16))
        *result = handle == &path_handle ? device_path : end_path;
    else if (handle == &fs_handle && !memcmp(guid, filesystem_guid, 16)) *result = &fs_handle;
    else if (handle != &path_handle || memcmp(guid, filesystem_guid, 16)) unexpected++;
    return *result ? 0 : 3;
}

static u64 locate_handles(unsigned type, const unsigned char *guid, void *key, u64 *count, void ***result)
{
    locates++;
    if (type != 2 || memcmp(guid, device_guid, 16) || key) { unexpected++; return 1; }
    *count = 2;
    *result = handles;
    return 0;
}

static u64 free_handles(void *allocation)
{
    releases++;
    if (allocation != handles) unexpected++;
    return 0;
}

int main(int argc, char **argv)
{
    if (argc != 2) return 2;
    /* Independent ABI layout: system slots are byte-offset/8, service slots are
     * header words + zero-based index. All neighbouring slots point at canaries.
     */
    u64 system[13] = { 0x5453595320494249ULL };
    u64 boot[48], decoy[48];
    for (unsigned i = 0; i < 48; i++) boot[i] = decoy[i] = (u64)canary;
    boot[0] = decoy[0] = 0x56524553544f4f42ULL;
    boot[19] = (u64)open_protocol;
    boot[39] = (u64)locate_handles;
    boot[9] = (u64)free_handles;
    text_output console = { 0, capture };
    system[8] = (u64)&console;
    system[9] = system[10] = system[11] = (u64)decoy;
    system[12] = (u64)boot;
    loaded_image[4] = end_path;
    if (!strcmp(argv[1], "bad-signature")) boot[0] ^= 1;
    else if (!strcmp(argv[1], "no-boot")) system[12] = 0;
    else if (strcmp(argv[1], "valid")) return 2;
    u64 status = probe_start(&image_handle, system);
    printf("{\"status\":%llu,\"opens\":%u,\"locates\":%u,\"releases\":%u,\"unexpected\":%u,\"output_hex\":\"",
           status, opens, locates, releases, unexpected);
    for (unsigned i = 0; i < output_length; i++) printf("%02x", (unsigned char)output[i]);
    printf("\"}\n");
    return 0;
}
