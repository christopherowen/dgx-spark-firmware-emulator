/* SPDX-License-Identifier: Apache-2.0
 * Original probe: list every handle that carries a device path and a file system, with raw path bytes.
 *
 * A `Driver####` load option has to name a file by device path, so the bytes have to come from the
 * firmware. This walks its handle database with `LocateHandleBuffer`, opens the device-path protocol on
 * each handle, and reports the ones that also carry `EFI_SIMPLE_FILE_SYSTEM_PROTOCOL` — those are the
 * volumes a file path can be appended to.
 *
 * Offsets that matter (both were wrong once): `EFI_SYSTEM_TABLE.BootServices` is at 0x60 and
 * `EFI_BOOT_SERVICES` entries start after a 24-byte header, 1-based, so `LocateHandleBuffer` is
 * `boot[3 + 37 - 1]` and `HandleProtocol` is `boot[3 + 17 - 1]`.
 *
 * Output: "FS-CNT <n>", then "FS <i> <len> <hex>" per file-system handle, then "FS-END".
 */
typedef unsigned short char16;
typedef unsigned long long u64;
typedef struct { void *Reset; u64 (*OutputString)(void *, const char16 *); } text_output;

static text_output *CON;
static void putc(char c) { char16 t[2] = { (char16)(unsigned char)c, 0 }; CON->OutputString(CON, t); }
static void puts(const char *s) { while (*s) putc(*s++); }
static void hex_byte(unsigned char v) { const char *d = "0123456789abcdef"; putc(d[v >> 4]); putc(d[v & 0xf]); }
static void number(u64 v) {
    char b[24]; int n = 0;
    if (!v) b[n++] = '0';
    while (v) { b[n++] = (char)('0' + v % 10); v /= 10; }
    while (n) putc(b[--n]);
}

#define BS_HEADER_WORDS 3
#define BS_HANDLE_PROTOCOL 17
#define BS_FREE_POOL 7
#define BS_LOCATE_HANDLE_BUFFER 37

static unsigned path_length(const unsigned char *node) {
    unsigned total = 0;
    for (;;) {
        unsigned length = node[2] | (node[3] << 8);
        if (length < 4 || total + length > 0x1000) return 0;
        total += length;
        if (node[0] == 0x7f && node[1] == 0xff) return total;
        node += length;
    }
}

unsigned long long _start(void *image, void *system_table) {
    (void)image;
    CON = *(text_output **)((unsigned char *)system_table + 0x40);
    if (!CON) return 1;
    void **boot = *(void ***)((unsigned char *)system_table + 0x60);
    if (!boot || *(u64 *)boot != 0x56524553544f4f42ULL) { puts("FS-FAIL boot services\n"); return 1; }

    typedef u64 (*locate_buffer_t)(unsigned, const unsigned char *, void *, u64 *, void ***);
    typedef u64 (*handle_protocol_t)(void *, const unsigned char *, void **);
    typedef u64 (*free_pool_t)(void *);
    locate_buffer_t locate = (locate_buffer_t)boot[BS_HEADER_WORDS + BS_LOCATE_HANDLE_BUFFER - 1];
    handle_protocol_t open = (handle_protocol_t)boot[BS_HEADER_WORDS + BS_HANDLE_PROTOCOL - 1];
    free_pool_t release = (free_pool_t)boot[BS_HEADER_WORDS + BS_FREE_POOL - 1];

    /* EFI_DEVICE_PATH_PROTOCOL 09576e91-6d3f-11d2-8e39-00a0c969723b
     * EFI_SIMPLE_FILE_SYSTEM_PROTOCOL 964e5b22-6459-11d2-8e39-00a0c969723b */
    static const unsigned char path_guid[16] = { 0x91, 0x6e, 0x57, 0x09, 0x3f, 0x6d, 0xd2, 0x11,
                                                 0x8e, 0x39, 0x00, 0xa0, 0xc9, 0x69, 0x72, 0x3b };
    static const unsigned char fs_guid[16] = { 0x22, 0x5b, 0x4e, 0x96, 0x59, 0x64, 0xd2, 0x11,
                                               0x8e, 0x39, 0x00, 0xa0, 0xc9, 0x69, 0x72, 0x3b };
    void **handles = 0;
    u64 count = 0;
    u64 status = locate(2 /* ByProtocol */, path_guid, 0, &count, &handles);
    if (status != 0 || !handles) {
        puts("FS-FAIL locate ");
        number(status);
        putc('\n');
        return 1;
    }
    puts("FS-HANDLES ");
    number(count);
    putc('\n');
    unsigned found = 0;
    for (u64 i = 0; i < count; i++) {
        void *path = 0, *fs = 0;
        if (open(handles[i], path_guid, &path) != 0 || !path) continue;
        int is_fs = open(handles[i], fs_guid, &fs) == 0 && fs != 0;
        unsigned length = path_length((const unsigned char *)path);
        if (!length) continue;
        puts(is_fs ? "FS " : "DP ");
        number(found);
        putc(' ');
        number(length);
        putc(' ');
        for (unsigned b = 0; b < length; b++) hex_byte(((const unsigned char *)path)[b]);
        putc('\n');
        if (is_fs) found++;
    }
    puts("FS-CNT ");
    number(found);
    putc('\n');
    release(handles);
    puts("FS-END\n");
    return 0;
}
