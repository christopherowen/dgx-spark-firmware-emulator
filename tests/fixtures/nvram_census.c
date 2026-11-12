/* SPDX-License-Identifier: Apache-2.0
 * Original AArch64 EFI application: enumerate the firmware's variables and its ACPI tables.
 *
 * Delivered only through the PXE/TFTP lane this repository already has. The point is to learn
 * from the firmware itself which settings exist — fan policy, memory configuration, platform
 * policy — and where their ACPI interface lives, without touching a machine.
 *
 * Output is one line per fact, prefixed so the harness can parse it:
 *   VAR <guid> <attributes> <size> <name>       (then DATA <size bytes> <hex> for small values)
 *   ACPI <signature> <length> <address>         (XSDT walk; the harness can dump the bytes)
 *   CENSUS-END <count>
 *
 * Written against the EFI structures directly, like the other probes here: the header offsets are
 * fixed by the specification, and a probe that needs a full EDK2 build would not be runnable from
 * a clean clone.
 */
typedef unsigned long long UINTN;
typedef unsigned long long UINT64;
typedef unsigned char UINT8;
typedef unsigned int UINT32;
typedef unsigned short UINT16;
typedef unsigned short CHAR16;
typedef void *EFI_HANDLE;
typedef UINTN EFI_STATUS;

typedef struct {
    UINT32 Data1;
    UINT16 Data2;
    UINT16 Data3;
    UINT8 Data4[8];
} GUID;

typedef struct {
    void *Reset;
    UINTN (*OutputString)(void *, const CHAR16 *);
} TEXT_OUTPUT;

typedef struct {
    unsigned char Header[0x48];   /* Hdr, GetTime .. ConvertPointer */
    /* Runtime services are plain functions: unlike protocol methods they take no This pointer. */
    UINTN (*GetVariable)(const CHAR16 *, const GUID *, UINT32 *, UINTN *, void *);
    UINTN (*GetNextVariableName)(UINTN *, CHAR16 *, GUID *);
    UINTN (*SetVariable)(const CHAR16 *, const GUID *, UINT32, UINTN, void *);
} RUNTIME_SERVICES_PREFIX;

typedef struct {
    unsigned char Header[0x58];
    RUNTIME_SERVICES_PREFIX *Runtime;
    unsigned char Boot[8];               /* BootServices pointer, 0x60..0x68 */
    UINTN NumberOfTableEntries;          /* 0x68 */
    void *ConfigurationTable;            /* 0x70 */
} SYSTEM_TABLE;

typedef struct {
    GUID VendorGuid;
    void *VendorTable;
} CONFIG_ENTRY;

typedef struct {
    UINT32 Signature;
    UINT32 Length;
    UINT8 Revision, Checksum, OemId[6], OemTableId[8];
    UINT32 OemRevision, CreatorId, CreatorRevision;
} ACPI_HEADER;

#define EFI_SUCCESS 0
#define EFI_NOT_FOUND 14
#define EFI_BUFFER_TOO_SMALL 5

static TEXT_OUTPUT *CON;
static SYSTEM_TABLE *ST;

static void emit(const char *s)
{
    /* The writer copies into a fixed buffer; callers keep their lines well inside this, and the
     * bound here is the backstop that turns a mistake into a truncated line rather than a crash. */
    static CHAR16 buf[256];
    int n = 0;
    while (s[n] && n < 254) { buf[n] = (CHAR16)(UINT8)s[n]; n++; }
    buf[n] = 0;
    CON->OutputString(CON, buf);
}

static char *append(char *p, const char *s)
{
    while (*s) *p++ = *s++;
    return p;
}

static char *append_hex(char *p, UINTN value, int digits)
{
    const char *hex = "0123456789abcdef";
    for (int shift = (digits - 1) * 4; shift >= 0; shift -= 4)
        *p++ = hex[(value >> shift) & 0xF];
    return p;
}

static void emit_guid(const GUID *guid)
{
    static char buf[80];
    char *p = buf;
    p = append_hex(p, guid->Data1, 8);
    *p++ = '-';
    p = append_hex(p, guid->Data2, 4);
    *p++ = '-';
    p = append_hex(p, guid->Data3, 4);
    *p++ = '-';
    for (int i = 0; i < 2; i++) { p = append_hex(p, guid->Data4[i], 2); }
    *p++ = '-';
    for (int i = 2; i < 8; i++) { p = append_hex(p, guid->Data4[i], 2); }
    *p = 0;
    emit(buf);
}

static const GUID ACPI2_GUID = { 0x8868e871, 0xe4f1, 0x11d3,
                                 { 0xbc, 0x22, 0x00, 0x80, 0xc7, 0x3c, 0x88, 0x81 } };
static const GUID ACPI1_GUID = { 0xeb9d2d30, 0x2d88, 0x11d3,
                                 { 0x9a, 0x16, 0x00, 0x90, 0x27, 0x3f, 0xc1, 0x4d } };

static int same_guid(const GUID *a, const GUID *b)
{
    const UINT8 *x = (const UINT8 *)a, *y = (const UINT8 *)b;
    for (int i = 0; i < 16; i++)
        if (x[i] != y[i])
            return 0;
    return 1;
}

static CHAR16 name[128];
static UINT8 data[8192];

static void walk_acpi(void)
{
    CONFIG_ENTRY *table = (CONFIG_ENTRY *)ST->ConfigurationTable;
    {
        static char line[64];
        char *p = line;
        p = append(p, "STAGE config-entries ");
        p = append_hex(p, ST->NumberOfTableEntries, 4);
        *p++ = '\n';
        *p = 0;
        emit(line);
    }
    for (UINTN i = 0; i < ST->NumberOfTableEntries; i++) {
        void *rsdp = 0;
        static char gl[64];
        char *gp = gl;
        gp = append(gp, "CFG ");
        *gp = 0;
        emit(gl);
        emit_guid(&table[i].VendorGuid);
        emit("\n");
        if (same_guid(&table[i].VendorGuid, &ACPI2_GUID) ||
            same_guid(&table[i].VendorGuid, &ACPI1_GUID))
            rsdp = table[i].VendorTable;
        if (!rsdp)
            continue;
        /* ACPI 2.0 RSDP: signature, checksum, OEM id, revision, rsdt, length, xsdt */
        UINT8 *bytes = (UINT8 *)rsdp;
        /* The signature is eight bytes, "RSD PTR ": the fourth character is a space, which is
         * exactly the check a four-byte comparison gets wrong. */
        if (!(bytes[0] == 'R' && bytes[1] == 'S' && bytes[2] == 'D' && bytes[3] == ' ' &&
              bytes[4] == 'P' && bytes[5] == 'T' && bytes[6] == 'R' && bytes[7] == ' '))
            continue;
        UINT64 xsdt = 0;
        for (int b = 7; b >= 0; b--)
            xsdt = (xsdt << 8) | bytes[0x18 + b];
        if (!xsdt)
            continue;
        ACPI_HEADER *root = (ACPI_HEADER *)xsdt;
        UINTN entries = (root->Length - sizeof(ACPI_HEADER)) / 8;
        for (UINTN e = 0; e < entries; e++) {
            UINT8 *entry = (UINT8 *)root + sizeof(ACPI_HEADER) + e * 8;
            UINT64 pointer = 0;
            for (int b = 7; b >= 0; b--)
                pointer = (pointer << 8) | entry[b];
            ACPI_HEADER *table_header = (ACPI_HEADER *)(UINTN)pointer;
            if (!table_header) continue;
            static char line[128];
            char *p = line;
            p = append(p, "ACPI ");
            for (int c = 0; c < 4; c++) *p++ = (char)(((UINT8 *)&table_header->Signature)[c]);
            *p++ = ' ';
            p = append_hex(p, table_header->Length, 8);
            *p++ = ' ';
            p = append_hex(p, (UINTN)table_header, 12);
            *p++ = '\n';
            *p = 0;
            emit(line);
        }
        /* The DSDT is not in the XSDT: it is pointed at from the FACP, and it is where a fan or
         * embedded-controller device would be described. Print it the same way, so the harness
         * captures it with the rest. */
        for (UINTN e = 0; e < entries; e++) {
            UINT8 *entry = (UINT8 *)root + sizeof(ACPI_HEADER) + e * 8;
            UINT64 pointer = 0;
            for (int b = 7; b >= 0; b--)
                pointer = (pointer << 8) | entry[b];
            ACPI_HEADER *table_header = (ACPI_HEADER *)(UINTN)pointer;
            if (table_header->Signature != 0x50434146)   /* 'FACP' */
                continue;
            UINT8 *facp = (UINT8 *)table_header;
            UINT64 dsdt = 0;
            if (table_header->Length >= 0x94) {
                for (int b = 7; b >= 0; b--)
                    dsdt = (dsdt << 8) | facp[0x8c + b];
            }
            if (!dsdt) {
                for (int b = 3; b >= 0; b--)
                    dsdt = (dsdt << 8) | facp[0x28 + b];
            }
            ACPI_HEADER *dsdt_header = (ACPI_HEADER *)(UINTN)dsdt;
            if (!dsdt_header)
                break;
            static char line[128];
            char *p = line;
            p = append(p, "ACPI DSDT ");
            p = append_hex(p, dsdt_header->Length, 8);
            *p++ = ' ';
            p = append_hex(p, (UINTN)dsdt_header, 12);
            *p++ = '\n';
            *p = 0;
            emit(line);
            break;
        }
        return;
    }
}

static void walk_variables(void)
{
    UINTN count = 0;
    UINTN size = sizeof(name);
    GUID guid;
    for (int i = 0; i < (int)sizeof(guid); i++) ((UINT8 *)&guid)[i] = 0;
    for (int i = 0; i < sizeof(name) / 2; i++) name[i] = 0;
    EFI_STATUS status = ST->Runtime->GetNextVariableName(&size, name, &guid);
    {
        static char line[64];
        char *p = line;
        p = append(p, "STAGE vars-status ");
        p = append_hex(p, status, 16);
        p = append(p, " size ");
        p = append_hex(p, size, 6);
        *p++ = '\n';
        *p = 0;
        emit(line);
    }
    while (status == EFI_SUCCESS && count < 512) {
        static char line[256];
        char *p = line;
        p = append(p, "VAR ");
        *p = 0;
        emit(line);
        emit_guid(&guid);
        UINT32 attributes = 0;
        UINTN length = sizeof(data);
        EFI_STATUS got = ST->Runtime->GetVariable(name, &guid, &attributes, &length, data);
        p = line;
        p = append(p, " ");
        p = append_hex(p, attributes, 8);
        *p++ = ' ';
        p = append_hex(p, length, 6);
        *p++ = ' ';
        /* the name, truncated to keep the line short */
        for (int c = 0; name[c] && c < 96; c++) *p++ = (char)(UINT8)name[c];
        *p++ = '\n';
        *p = 0;
        emit(line);
        if (got == EFI_SUCCESS && length <= 512) {
            /* Hex goes out in fixed-size lines: a 512-byte value is 1024 characters, which would
             * overrun both the formatting buffer and the console writer's own copy. */
            UINTN limit = length < 512 ? length : 512;
            for (UINTN start = 0; start < limit; start += 48) {
                p = line;
                p = append(p, "DATA ");
                for (UINTN b = start; b < limit && b < start + 48; b++)
                    p = append_hex(p, data[b], 2);
                *p++ = '\n';
                *p = 0;
                emit(line);
            }
        }
        count++;
        /* The name and the GUID are the cursor: clearing them would restart the enumeration. */
        size = sizeof(name);
        status = ST->Runtime->GetNextVariableName(&size, name, &guid);
    }
    static char line[64];
    char *p = line;
    p = append(p, "CENSUS-END ");
    p = append_hex(p, count, 4);
    *p++ = '\n';
    *p = 0;
    emit(line);
}

EFI_STATUS _start(EFI_HANDLE image, SYSTEM_TABLE *table)
{
    (void)image;
    ST = table;
    CON = *(TEXT_OUTPUT **)((UINT8 *)table + 0x40);
    if (!CON || !table->Runtime)
        return 1;
    emit("CENSUS-BEGIN\n");
    {
        static char line[64];
        char *p = line;
        p = append(p, "STAGE runtime ");
        p = append_hex(p, (UINTN)table->Runtime, 12);
        *p++ = '\n';
        *p = 0;
        emit(line);
    }
    walk_acpi();
    emit("STAGE acpi-done\n");
    walk_variables();
    for (;;)
        __asm__ volatile("wfi");
    return 0;
}
