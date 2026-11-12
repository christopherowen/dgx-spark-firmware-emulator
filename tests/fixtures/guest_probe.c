/* SPDX-License-Identifier: Apache-2.0
 * A guest-side probe used as `/init`: it mounts what it needs, loads the modules the vendor init
 * would have loaded, prints a few answers, and stops.
 *
 * Why it is a static ELF: the vendor kernel's own `/init` lives in an embedded initramfs with no
 * shell on any path an injected image can reach (`/bin`, `/sbin` and `/usr/bin` do not exist, and `/`
 * refuses O_DIRECTORY), so `init=/bin/sh` is ignored and a script has nothing to interpret it. Raw
 * syscalls only, no libc.
 *
 * Why it loads modules: replacing `/init` also removes the userspace that loads them, and without
 * them no PCIe driver probes — which is exactly the behaviour this probe exists to observe. The
 * `.ko` files are expanded on the host before the archive is built, because a freestanding binary
 * cannot decompress zstd.
 *
 * It answers one question: why does the NVMe driver return -EPROBE_DEFER for a device the guest has
 * already enumerated? Measured, and not what the code alone suggests: the device carries **no
 * `supplier:*` device links**, so it is not waiting on the SMMU or on any other device. The deferral
 * comes from the driver's own probe, the kernel's `deferred_probe_timeout` eventually "ignores the
 * dependency", and the probe then succeeds — which is why the loader sets that timeout to one second.
 */
#define SYS_mount        40
#define SYS_mkdirat      34
#define SYS_openat       56
#define SYS_close        57
#define SYS_getdents64   61
#define SYS_read         63
#define SYS_write        64
#define SYS_nanosleep   101
#define SYS_finit_module 273

static long sys(long number, long a, long b, long c, long d, long e, long f)
{
    register long x8 __asm__("x8") = number;
    register long x0 __asm__("x0") = a;
    register long x1 __asm__("x1") = b;
    register long x2 __asm__("x2") = c;
    register long x3 __asm__("x3") = d;
    register long x4 __asm__("x4") = e;
    register long x5 __asm__("x5") = f;
    __asm__ volatile("svc #0" : "+r"(x0) : "r"(x8), "r"(x1), "r"(x2), "r"(x3), "r"(x4), "r"(x5)
                     : "memory");
    return x0;
}

static unsigned long length(const char *s)
{
    unsigned long n = 0;
    while (s[n]) n++;
    return n;
}

static void puts_(const char *s)
{
    sys(SYS_write, 1, (long)s, (long)length(s), 0, 0, 0);
}

static void show_number(const char *label, long value)
{
    char digits[24];
    int i = 23;
    int negative = value < 0;
    unsigned long v = negative ? -(unsigned long)value : (unsigned long)value;
    digits[i] = 0;
    if (!v) digits[--i] = '0';
    while (v) { digits[--i] = (char)('0' + v % 10); v /= 10; }
    puts_(label);
    if (negative) puts_("-");
    puts_(&digits[i]);
}

/* List a directory with plain O_RDONLY: O_DIRECTORY is refused (EINVAL) on this rootfs, and
 * getdents does not need it. The PCI device's directory carries its `supplier:*` links, which is the
 * only place the kernel names what a deferred probe is waiting for. */
static void list(const char *path)
{
    char buffer[4096];
    puts_("\n--- ls ");
    puts_(path);
    long fd = sys(SYS_openat, -100, (long)path, 0, 0, 0, 0);
    if (fd < 0) {
        show_number(" (open returned ", fd);
        return;
    }
    long got;
    while ((got = sys(SYS_getdents64, fd, (long)buffer, sizeof buffer, 0, 0, 0)) > 0) {
        for (long i = 0; i < got;) {
            unsigned short reclen = *(unsigned short *)(buffer + i + 16);
            const char *name = buffer + i + 19;
            if (name[0] != '.') {
                puts_(" ");
                puts_(name);
            }
            i += reclen;
        }
    }
    sys(SYS_close, fd, 0, 0, 0, 0, 0);
}

static void show(const char *path)
{
    char buffer[4096];
    long fd = sys(SYS_openat, -100, (long)path, 0, 0, 0, 0);
    puts_("\n--- ");
    puts_(path);
    puts_("\n");
    if (fd < 0) {
        puts_("(cannot open)\n");
        return;
    }
    long got;
    while ((got = sys(SYS_read, fd, (long)buffer, sizeof buffer, 0, 0, 0)) > 0)
        sys(SYS_write, 1, (long)buffer, got, 0, 0, 0);
    sys(SYS_close, fd, 0, 0, 0, 0, 0);
}

static void mount_(const char *type, const char *target)
{
    sys(SYS_mkdirat, -100, (long)target, 0755, 0, 0, 0);   /* EEXIST is fine */
    long result = sys(SYS_mount, (long)type, (long)target, (long)type, 0, 0, 0);
    puts_("\nmount ");
    puts_(target);
    show_number(" -> ", result);
}

static void load(const char *path)
{
    long fd = sys(SYS_openat, -100, (long)path, 0, 0, 0, 0);
    puts_("\nfinit_module ");
    puts_(path);
    if (fd < 0) {
        puts_(" (cannot open)");
        return;
    }
    show_number(" -> ", sys(SYS_finit_module, fd, (long)"", 0, 0, 0, 0));
    sys(SYS_close, fd, 0, 0, 0, 0, 0);
}

static void nap(long seconds)
{
    struct { long sec; long nsec; } nap = {seconds, 0};
    sys(SYS_nanosleep, (long)&nap, 0, 0, 0, 0, 0);
}

void start_c(void)
{
    puts_("\n=== GUEST-PROBE-BEGIN");
    mount_("proc", "/proc");
    mount_("sysfs", "/sys");
    mount_("debugfs", "/sys/kernel/debug");
    show("/proc/cmdline");
    /* The vendor userspace is what loads the drivers, and replacing /init removed it. */
    load("/nvme-keyring.ko");
    load("/hkdf.ko");
    load("/nvme-auth.ko");
    load("/nvme-core.ko");
    load("/nvme.ko");
    /* Read the list while the device is still waiting: once the deferred-probe timeout expires the
     * kernel "ignores the dependency" and probes anyway, which erases the evidence. */
    nap(2);
    show("/sys/kernel/debug/devices_deferred");
    list("/sys/bus/pci/devices/000f:00:02.0");
    puts_("\n--- waiting 20 s for the probes");
    nap(20);
    show("/sys/kernel/debug/devices_deferred");
    show("/sys/bus/pci/devices/000f:00:02.0/driver");
    show("/sys/bus/pci/devices/000f:00:02.0/irq");
    show("/sys/kernel/iommu_groups/groups");
    puts_("\n=== GUEST-PROBE-END\n");
    for (;;) nap(3600);
}

__attribute__((naked)) void _start(void)
{
    __asm__ volatile("bl start_c\n"
                     "1: b 1b\n");
}
