/* SPDX-License-Identifier: Apache-2.0
 * Original probe: print the device path this image was loaded from, as raw bytes.
 *
 * A `Driver####` load option has to carry a device path to the file it loads, and the only trustworthy
 * source for that path is the firmware itself. Our own image handle publishes `EFI_DEVICE_PATH_PROTOCOL`,
 * so one `HandleProtocol` call answers it.
 *
 * Two offsets matter and both were wrong once: in `EFI_SYSTEM_TABLE`, `RuntimeServices` is at 0x58 and
 * `BootServices` at 0x60 (0x48/0x50 are the standard-error handle and its text-output protocol, whose
 * first field is a function pointer — which is why the wrong offset still looked plausible). In
 * `EFI_BOOT_SERVICES`, `HandleProtocol` is index 17 after the 24-byte header.
 *
 * Output: "SELF <length> <hex bytes>", then "SELF-END".
 */
typedef unsigned short char16;
typedef unsigned long long u64;

typedef struct { void *Reset; u64 (*OutputString)(void *, const char16 *); } text_output;

typedef struct {
    u64 signature;
    unsigned revision;
    unsigned header_size;
    unsigned crc32;
    unsigned reserved;
} table_header;

static text_output *CON;
static void putc(char c) { char16 text[2] = { (char16)(unsigned char)c, 0 }; CON->OutputString(CON, text); }
static void puts(const char *text) { while (*text) putc(*text++); }

static void hex_byte(unsigned char value) {
    const char *digits = "0123456789abcdef";
    putc(digits[value >> 4]);
    putc(digits[value & 0xf]);
}

static void number(u64 value) {
    char buffer[24];
    int length = 0;
    if (!value) buffer[length++] = '0';
    while (value) { buffer[length++] = (char)('0' + value % 10); value /= 10; }
    while (length) putc(buffer[--length]);
}

/* EFI_BOOT_SERVICES: header (24 bytes) then function pointers; HandleProtocol is the 17th. */
#define BOOT_SERVICES_HANDLE_PROTOCOL_INDEX 17
#define BOOT_SERVICES_HEADER_BYTES 24

static u64 path_length(const unsigned char *node) {
    unsigned total = 0;
    for (;;) {
        unsigned length = node[2] | (node[3] << 8);
        if (length < 4 || total + length > 0x1000)
            return 0;
        total += length;
        if (node[0] == 0x7f && node[1] == 0xff)          /* end of the whole path */
            return total;
        node += length;
    }
}

unsigned long long _start(void *image, void *system_table) {
    CON = *(text_output **)((unsigned char *)system_table + 0x40);
    if (!CON)
        return 1;
    if (((table_header *)system_table)->signature != 0x5453595320494249ULL) {
        puts("SELF-FAIL system table\n");
        return 1;
    }
    void **boot = *(void ***)((unsigned char *)system_table + 0x60);
    if (!boot || *(u64 *)boot != 0x56524553544f4f42ULL) {   /* "BOOTSERV" */
        puts("SELF-FAIL boot services\n");
        return 1;
    }
    typedef u64 (*handle_protocol_t)(void *, const unsigned char *, void **);
    /* functions begin after the 3-word header, and indices are 1-based: 3 + 17 - 1 = 19 */
    handle_protocol_t open = (handle_protocol_t)boot[BOOT_SERVICES_HEADER_BYTES / 8 +
                                                    BOOT_SERVICES_HANDLE_PROTOCOL_INDEX - 1];
    /* The image handle does not publish the device-path protocol on this firmware (HandleProtocol
     * answers EFI_UNSUPPORTED), but it does publish EFI_LOADED_IMAGE_PROTOCOL, whose FilePath field is
     * the same path. GUID 5b1b31a1-9562-11d2-8e3f-00a0c969723b. */
    static const unsigned char loaded_image_guid[16] = { 0xa1, 0x31, 0x1b, 0x5b, 0x62, 0x95, 0xd2, 0x11,
                                                         0x8e, 0x3f, 0x00, 0xa0, 0xc9, 0x69, 0x72, 0x3b };
    void *loaded = 0;
    u64 status = open(image, loaded_image_guid, &loaded);
    if (status != 0 || !loaded) {
        puts("SELF-FAIL loaded-image ");
        number(status);
        putc('\n');
        return 1;
    }
    /* struct EFI_LOADED_IMAGE_PROTOCOL: Revision u32 at 0, then ParentHandle 0x08, SystemTable 0x10,
     * DeviceHandle 0x18, FilePath 0x20. */
    void *path = *(void **)((unsigned char *)loaded + 0x20);
    if (!path) {
        puts("SELF-FAIL file-path\n");
        return 1;
    }
    unsigned length = path_length((const unsigned char *)path);
    if (!length) {
        puts("SELF-FAIL length\n");
        return 1;
    }
    puts("SELF ");
    number(length);
    putc(' ');
    for (unsigned i = 0; i < length; i++)
        hex_byte(((const unsigned char *)path)[i]);
    putc('\n');
    puts("SELF-END\n");
    return 0;
}
