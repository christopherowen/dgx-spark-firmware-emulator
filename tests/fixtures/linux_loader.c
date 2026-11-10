/* An EFI application that starts the vendor recovery kernel from the attached volume.
 *
 * The recovery image normally boots through grub, and grub is what supplies the kernel's command
 * line and the initrd path. Under this profile the firmware's own boot order has nothing to match,
 * so a probe delivered over PXE does that job: it opens the volume it was pointed at, loads
 * `vmlinuz.Image` (a PE32+ image with the kernel's EFI stub), starts it with the recovery command
 * line, and reports the status if the kernel ever hands control back.
 *
 * Every structure here is written out by offset on purpose: the header layouts are the UEFI 2.x
 * definitions, and naming the offsets is what makes a mistake visible.
 */

typedef unsigned long long u64;
typedef unsigned long long efi_status;
typedef unsigned short char16;
typedef void *efi_handle;

typedef struct {
    char pad[24];
    void *firmware_vendor;
    unsigned firmware_revision[2];
    char rest[0];
} efi_table_header;

typedef struct efi_boot_services {
    char hdr[24];
    void *raise_tpl;
    void *restore_tpl;
    void *allocate_pages;
    void *free_pages;
    void *get_memory_map;
    efi_status (*allocate_pool)(unsigned type, u64 size, void **buffer);
    void *free_pool;
    void *create_event;
    void *set_timer;
    void *wait_for_event;
    void *signal_event;
    void *close_event;
    void *check_event;
    void *install_protocol_interface;
    void *reinstall_protocol_interface;
    void *uninstall_protocol_interface;
    efi_status (*handle_protocol)(efi_handle handle, void *guid, void **interface);
    void *reserved;
    void *register_protocol_notify;
    efi_status (*locate_handle)(unsigned search_type, void *guid, void *search_key,
                                u64 *count, efi_handle *buffer);
    void *locate_device_path;
    void *install_configuration_table;
    efi_status (*load_image)(int policy, efi_handle parent, void *device_path, void *source,
                             u64 source_size, char16 *options);
    efi_status (*start_image)(efi_handle image, u64 *exit_size, char16 **exit_data);
    void *exit;
    efi_status (*unload_image)(efi_handle image);
    void *exit_boot_services;
} efi_boot_services;

typedef struct {
    char hdr[24];
    void *firmware_vendor;
    unsigned firmware_revision;
    unsigned pad;
    void *console_in_handle;
    void *con_in;
    void *console_out_handle;
    void *con_out;
    void *standard_error_handle;
    void *std_err;
    void *runtime_services;
    efi_boot_services *boot_services;
} efi_system_table;

typedef struct {
    unsigned revision;
    unsigned pad;
    efi_handle parent;
    efi_system_table *system_table;
    efi_handle device_handle;
    void *file_path;
    void *reserved;
    unsigned load_options_size;
    char16 *load_options;
} efi_loaded_image;

typedef struct efi_file {
    u64 revision;
    efi_status (*open)(struct efi_file *self, struct efi_file **file, char16 *name,
                       u64 mode, u64 attributes);
    efi_status (*close)(struct efi_file *self);
    void *delete_;
    efi_status (*read)(struct efi_file *self, u64 *size, void *buffer);
    void *write;
    void *get_position;
    void *set_position;
    efi_status (*get_info)(struct efi_file *self, void *info, u64 *size);
    void *set_info;
    void *flush;
} efi_file;

typedef struct {
    u64 revision;
    efi_status (*open_volume)(void *self, efi_file **root);
} simple_file_system;

/* EFI_SIMPLE_FILE_SYSTEM_PROTOCOL and EFI_LOADED_IMAGE_PROTOCOL, byte for byte. */
static const unsigned char sfsp_guid[16] = { 0x22, 0x5b, 0x4e, 0x96, 0x59, 0x64, 0xd2, 0x11,
                                             0x8e, 0x39, 0x00, 0xa0, 0xc9, 0x69, 0x72, 0x3b };
/* EFI_LOADED_IMAGE_PROTOCOL: 5B1B31A1-9562-11D2-8E3F-00A0C969723B, in mixed-endian byte order. */
static const unsigned char li_guid[16] = { 0xa1, 0x31, 0x1b, 0x5b, 0x62, 0x95, 0xd2, 0x11,
                                           0x8e, 0x3f, 0x00, 0xa0, 0xc9, 0x69, 0x72, 0x3b };
static const unsigned char dp_guid[16] = { 0x91, 0x6e, 0x57, 0x09, 0x3f, 0x6d, 0xd2, 0x11,
                                           0x8e, 0x39, 0x00, 0xa0, 0xc9, 0x69, 0x72, 0x3b };

static efi_boot_services *bs;
static void *con_out;

static void put(char16 *text)
{
    /* EFI_SIMPLE_TEXT_OUTPUT_PROTOCOL: Reset, then OutputString. */
    efi_status (*output)(void *, char16 *) = *(efi_status (**)(void *, char16 *))((char *)con_out + 0x08);
    (void)output(con_out, text);
}

static void say(const char *text)
{
    char16 buffer[256];
    int i = 0;
    while (text[i] && i < 255) {
        buffer[i] = (char16)text[i];
        i++;
    }
    buffer[i] = 0;
    put(buffer);
}

static void hex(u64 value)
{
    static const char digits[] = "0123456789abcdef";
    char16 out[19];
    out[0] = '0';
    out[1] = 'x';
    for (int i = 0; i < 16; i++)
        out[2 + i] = (char16)digits[(value >> (60 - 4 * i)) & 0xf];
    out[18] = 0;
    put(out);
}

static char16 *ascii_to_utf16(const char *text)
{
    char16 *out = 0;
    int length = 0;
    while (text[length])
        length++;
    if (bs->allocate_pool(0 /* EfiLoaderData */, (u64)(length + 1) * 2, (void **)&out) != 0)
        return 0;
    for (int i = 0; i <= length; i++)
        out[i] = (char16)text[i];
    return out;
}

static efi_status read_file(efi_file *root, char16 *name, void **data, u64 *size)
{
    efi_file *file = 0;
    efi_status status = root->open(root, &file, name, 1 /* read */, 0);
    if (status != 0)
        return status;
    /* The size comes from read itself: a buffer of the image's own order of magnitude, filled in
     * chunks, so nothing here depends on the file-info protocol agreeing with this firmware. */
    u64 capacity = 128ull * 1024 * 1024;
    status = bs->allocate_pool(0, capacity, data);
    if (status != 0) {
        file->close(file);
        return status;
    }
    u64 total = 0;
    while (total < capacity) {
        u64 chunk = capacity - total;
        if (chunk > 8ull * 1024 * 1024)
            chunk = 8ull * 1024 * 1024;
        status = file->read(file, &chunk, (char *)*data + total);
        if (status != 0)
            break;
        if (chunk == 0)
            break;
        total += chunk;
    }
    file->close(file);
    if (status != 0)
        return status;
    *size = total;
    return 0;
}

efi_status _start(efi_handle image, efi_system_table *table)
{
    bs = table->boot_services;
    con_out = table->con_out;
    say("LOADER-START\r\n");

    /* Find the volume that carries the kernel rather than assuming which one it is: under this
     * profile the firmware's own boot order has nothing to match, so the probe is the only thing
     * that knows where the recovery files were put. */
    /* The count is an in-out parameter: a buffer that is too small comes back as
     * EFI_BUFFER_TOO_SMALL with the required count filled in, and that is not the same thing as a
     * firmware with no file systems at all — a distinction the first version of this probe lost. */
    efi_handle handles[64];
    u64 count = sizeof(handles) / sizeof(handles[0]);
    efi_status locate = bs->locate_handle(2 /* by protocol */, (void *)sfsp_guid, 0, &count, handles);
    if (locate != 0) {
        say("LOADER-LOCATE-FAIL ");
        hex(locate);
        say(" ");
        hex(count);
        say("\r\n");
        return 1;
    }
    say("LOADER-VOLUMES ");
    hex(count);
    say("\r\n");

    char16 *kernel_name = 0;
    for (unsigned i = 0; i < count; i++) {
        void *protocol = 0;
        if (bs->handle_protocol(handles[i], (void *)sfsp_guid, &protocol) != 0)
            continue;
        simple_file_system *fs = protocol;
        efi_file *root = 0;
        if (fs->open_volume(fs, &root) != 0)
            continue;
        kernel_name = ascii_to_utf16("\\VMLINUZ.IMG");
        efi_file *probe = 0;
        if (root->open(root, &probe, kernel_name, 1, 0) == 0) {
            probe->close(probe);
            say("LOADER-KERNEL-ON-VOLUME ");
            hex(i);
            say("\r\n");
            break;
        }
        root->close(root);
        kernel_name = 0;
    }
    if (!kernel_name) {
        say("LOADER-NO-KERNEL\r\n");
        return 1;
    }

    /* The kernel and its initrd live on the same volume, so the file system handle is looked up
     * again and both are read from it. */
    void *protocol = 0;
    efi_handle volume = 0;
    for (unsigned i = 0; i < count; i++) {
        if (bs->handle_protocol(handles[i], (void *)sfsp_guid, &protocol) != 0)
            continue;
        simple_file_system *fs = protocol;
        efi_file *root = 0;
        if (fs->open_volume(fs, &root) != 0)
            continue;
        efi_file *probe = 0;
        if (root->open(root, &probe, kernel_name, 1, 0) == 0) {
            probe->close(probe);
            volume = handles[i];
            protocol = fs;
            break;
        }
        root->close(root);
    }
    if (!volume) {
        say("LOADER-NO-VOLUME\r\n");
        return 1;
    }
    simple_file_system *fs = protocol;
    efi_file *root = 0;
    if (fs->open_volume(fs, &root) != 0) {
        say("LOADER-VOLUME-FAIL\r\n");
        return 1;
    }

    void *kernel = 0;
    u64 kernel_size = 0;
    efi_status kernel_status = read_file(root, kernel_name, &kernel, &kernel_size);
    if (kernel_status != 0) {
        say("LOADER-KERNEL-READ-FAIL ");
        hex(kernel_status);
        say("\r\n");
        return 1;
    }
    say("LOADER-KERNEL ");
    hex(kernel_size);
    say("\r\n");

    void *initrd = 0;
    u64 initrd_size = 0;
    char16 *initrd_name = ascii_to_utf16("\\INITRD");
    if (read_file(root, initrd_name, &initrd, &initrd_size) != 0) {
        say("LOADER-INITRD-READ-FAIL\r\n");
        return 1;
    }
    say("LOADER-INITRD ");
    hex(initrd_size);
    say("\r\n");

    /* The volume's own device path is what lets the kernel's EFI stub resolve `initrd=`: the path of
     * this probe's image belongs to the network volume and would point at the wrong place. */
    void *device_path = 0;
    (void)bs->handle_protocol(volume, (void *)dp_guid, &device_path);

    /* The firmware's SPCR describes a 16550 at 0x16a00000 with 32-bit registers, which is what the
     * profile models; naming it explicitly gets output before the driver probes. */
    /* keep_bootcon: the real 8250 takes the console over and the profile's UART interrupts are not
     * what it waits for, so the early console stays to keep a stall visible.
     * nokaslr: a sampled or disassembled address then names a kernel offset.
     * initcall_debug: a boot that stops printing names the initialiser it stopped in.
     * efi=noruntime: this profile does not implement the firmware's runtime variable service (it
     * answers FF-A direct requests to endpoint 0x8004 with "unsupported"), so the kernel's
     * efivar_init spins; without runtime services the boot proceeds. */
    char16 *options = ascii_to_utf16("console=ttyS0,115200 earlycon=uart8250,mmio32,0x16a00000 acpi=force "
                                     "keep_bootcon nokaslr initcall_debug "
                                     "efi=noruntime ignore_loglevel initrd=\\INITRD");
    /* This firmware refuses LoadImage with a NULL parent image handle, and refuses a file-system
     * handle as parent; an image handle is required, and that is the one thing about the call a
     * reader of the UEFI specification would not predict. The volume's device path rides along so
     * the kernel's EFI stub can resolve `initrd=` against the volume the files came from. */
    efi_status (*load)(int, efi_handle, void *, void *, u64, efi_handle *) =
        *(efi_status (**)(int, efi_handle, void *, void *, u64, efi_handle *))
        ((char *)bs + 0xc8);

    efi_handle kernel_image = 0;
    efi_status status = load(0, image, device_path, kernel, kernel_size, &kernel_image);
    if (status != 0) {
        say("LOADER-LOAD-PATH-FAIL ");
        hex(status);
        say("\r\n");
        status = load(0, image, 0, kernel, kernel_size, &kernel_image);
    }
    if (status != 0) {
        say("LOADER-LOAD-FAIL ");
        hex(status);
        say("\r\n");
        return 1;
    }
    say("LOADER-LOADED ");
    hex((u64)kernel_image);
    say("\r\n");

    /* The command line is not a LoadImage argument: it is the loaded image's LoadOptions. */
    efi_loaded_image *loaded = 0;
    if (bs->handle_protocol(kernel_image, (void *)li_guid, (void **)&loaded) == 0 && loaded) {
        u64 length = 0;
        while (options[length])
            length++;
        loaded->load_options_size = (unsigned)((length + 1) * 2);
        loaded->load_options = options;
        say("LOADER-OPTIONS ");
        hex(loaded->load_options_size);
        say("\r\n");
    } else {
        say("LOADER-NO-LOADED-IMAGE\r\n");
    }

    say("LOADER-STARTING\r\n");
    u64 exit_size = 0;
    char16 *exit_data = 0;
    status = bs->start_image(kernel_image, &exit_size, &exit_data);
    say("LOADER-KERNEL-RETURNED ");
    hex(status);
    say("\r\n");
    return status;
}
