# Booting the recovery Linux under the Spark profile

`tools/run_linux.py` boots the vendor recovery kernel under the plain `virt` machine and reaches
userspace. Booting the same kernel *under the firmware profile* — the vendor UEFI on top of this
repository's monitor — is a different question, and this page records how far that gets and what
stops it. Nothing here is a claim that the profile runs Linux; the boundary is stated at the end.

## Why a loader at all

The recovery image boots through grub, which is what supplies the kernel's command line and the
initrd path. Under the profile the firmware's own boot order matches nothing on the emulated NVMe
namespace, so a probe delivered over the network does that job. `tests/fixtures/linux_loader.c` is
that probe: it finds the volume that carries the kernel, loads `VMLINUZ.IMG` and `INITRD`, starts the
kernel with the recovery command line, and returns the status if control ever comes back.

Four things about that call are not what the UEFI specification would lead a reader to expect, each
learned from a run:

- **A parent image handle is required.** `LoadImage` with a NULL parent is refused with
  `EFI_INVALID_PARAMETER`, as is a file-system handle. Passing the loader's own image handle works.
- **The command line is not a `LoadImage` argument.** It is the `LoadOptions` field of the loaded
  image's `EFI_LOADED_IMAGE_PROTOCOL`, set between `LoadImage` and `StartImage`.
- **`initrd=` resolves against the loaded image's device path.** The loader hands `LoadImage` the
  *volume's* device path so that `initrd=\INITRD` finds the volume the files came from, not the
  network volume the probe arrived on.
- **`unsigned long` is 32 bits on this target's ABI.** A request word packed with `<< 40` in a `long`
  truncates silently; the fixtures use an explicit 64-bit type.

## The volume

`tools/make_esp.py` writes the FAT32 image from files given on the command line, without mkfs or
mtools:

```sh
python3 tools/make_esp.py --out firmware/state/esp.img \
    --add VMLINUZ.IMG=firmware/downloads/recovery-extract/vmlinuz.Image \
    --add INITRD=firmware/downloads/recovery-extract/initrd
python3 tools/boot_spark.py --pxe-test --board --flash-image firmware/state/board-2158.bin \
    --firmware-version 2.158.15 --pxe-image loader.efi --pxe-marker LOADER-STARTING \
    --nvme-image firmware/state/esp.img --memory 3G
```

The volume carries what a FAT32 file system requires and what a UEFI driver checks: at least 65525
clusters, reserved-sector FAT entries, a backup boot sector, an FSInfo sector.

The cluster floor is the one that bites. The first image this tool wrote used 4 KiB clusters in
238 MB, which is 58 500 clusters — fewer than FAT32 requires. mtools read it, `mkfs.vfat` would have
written it, and the firmware presented **no volume at all**: the loader's `LocateHandle` came back
`EFI_NOT_FOUND`, which reads like a broken loader rather than a malformed volume. With 512-byte
clusters (162 MB total for the same two files) the same loader finds eight file systems, reads the
kernel and its initrd and starts it (`firmware/runs/20261108T211101`, `LOADER-STARTING`).

`tests/test_make_esp.py` reads the image back with an independent parser, checks the cluster count
against the boot sector's own numbers, and opens the image under mtools when mtools is installed.

## What the profile does today

The kernel boots. Recorded run (`firmware/runs/20261109T035647.028894Z`, 3 GiB guest):

```
LOADER-START
LOADER-VOLUMES 0x0000000000000008
LOADER-KERNEL 0x0000000004378188      # 70 746 504 bytes
LOADER-INITRD 0x0000000004f04202      # 82 854 402 bytes
LOADER-LOADED 0x0000000082808b98
LOADER-OPTIONS 0x000000000000007e
LOADER-STARTING
[    0.000000] Booting Linux on physical CPU 0x0000000000 [0x000f0510]
[    0.000000] Linux version 7.0.0-1018-nvidia ...
[    0.000000] earlycon: uart8250 at MMIO32 0x0000000016a00000 (options '')
[    0.000000] Kernel command line: console=ttyS0,115200 earlycon=uart8250,mmio32,0x16a00000 ...
[    2.184580] scmi_core: SCMI protocol bus registered
```

Getting there took four measured steps, each of which had to be fixed where it lived:

- **The secure monitor must enable the registers a current kernel writes.** The kernel programs
  `PIR_EL1`/`PIRE0_EL1` (permission indirection), `GCSCR_EL1`/`GCSCRE0_EL1` (guarded control
  stack), `HCRX_EL2`, the fine-grained trap registers, `TCR2_EL1` and `SCTLR2_EL2` during its
  early boot. Each is gated by an `SCR_EL3` enable — `PIEN` (45), `GCSEN` (39), `HXEN` (38),
  `FGTEN` (27), `TCR2EN` (43), `SCTLR2En` (44) — and with a bit clear the access traps to EL3.
  Finding that list was the hard part and it was found by instrumenting QEMU, not by guessing: the
  emulator now logs any system-register access routed to EL3, with the register's name and the
  trap controls, so the next one names itself.
- **The console has to be named.** The firmware's SPCR describes a 16550 with 32-bit registers at
  `0x16a00000` — exactly what the profile models — but a kernel booted through `LoadImage` has no
  device tree to tell it that, so `earlycon=uart8250,mmio32,0x16a00000` goes in the command line.
  Without it the boot is silent and looks dead.
- **The ITS was at the wrong address.** The captured MADT declares it (`record type 0x0f`, ID 0) at
  `0x06840000`, while the inherited `virt` machine mapped QEMU's ITS at `0x08080000`; the kernel
  trusted the table, found nothing at the address it was given, and its first ITS table write was an
  external abort in `its_map_one`. Relocating the model to the declared address fixed it — the boot
  now reports `ITS [mem 0x06840000-0x0685ffff]`, allocates device and collection tables, and reaches
  userspace **without** `irqchip.gicv3_nolpi=1`.
- **`_CPC` is read at boot, so the block that holds it has to exist.** `\_SB.MTEL` declares
  `Memory32Fixed(0x05170000, 0x00060000)`, and ACPICA's system-memory handler faults on any address
  in it that the machine does not back. The profile now models that block — zero-backed, because
  the values are what the firmware would have programmed and this profile does not claim to know
  them. Before that, the abort landed in `acpi_ex_system_memory_space_handler` with `x19 =
  0x51cc404`, the first `_CPC` register.

## Where it stops

The boot reaches `[3.01] scmi_core: SCMI protocol bus registered` and then produces nothing for
twelve minutes of wall time, on two runs and with `keep_bootcon`. The vCPU thread in QEMU
accumulates CPU time throughout, so the guest is executing, not waiting — a loop in code rather than
an MMIO wait (an MMIO exit would release the BQL and a second QMP connection would answer; it does
not).

The `scmi_core` message is `scmi_bus_init()`, a bus registration, not the transport handshake, so
the stall is in some *later* subsystem initialiser and the last line is only where printing stopped.
Finding it needs the guest's PC, which is why the trace plugin grew a sampler
(`--trace-sample N`: the address of every N-th translated basic block, through a per-block execution
callback so an already-translated hot loop still samples).

## It reaches userspace

Recorded run (`firmware/runs/20261109T055050.743794Z`, 3 GiB guest):

```
[   25.430779] Freeing initrd memory: 80912K
[   28.070124] Run /init as init process
--- FastOS Version
VERSION="1.155.49"
```

That is the vendor's recovery installer running as init, under the vendor UEFI, under this
repository's monitor. Three more things had to be right:

- **The monitor must not dereference a guest address.** A trapped system-register access was being
  decoded by reading the instruction at `ELR_EL3`; once the guest had its MMU on, that is a guest
  virtual address that EL3 cannot translate, and the monitor itself took the abort
  (`FAR=0xffff80008001922c`, EL3 faulting). The register is now named from the syndrome
  (`ESR_EL3`'s ISS carries Op0/Op1/CRn/CRm/Op2 and Rt), so nothing is read from guest memory.
- **An SMMU the profile does not model must read as absent, not abort.** The kernel's
  `arm_smmu_device_hw_probe` aborted on the first identification register. Reads of unmodelled SoC
  windows now return zero — `arm-smmu-v3` then reports `probe ... returned 6` (-ENXIO) and the boot
  continues.
- **Unmodelled SoC MMIO is inert by default.** `0x10000000-0x1F000000` and `0x30000000-0x32000000`
  are mapped as a read-zero/write-drop window *under* the modelled devices, so a driver that probes
  an address the firmware names finds "no device" instead of taking the machine down.

## Every block the firmware declares has to exist

A kernel reads ACPI-declared memory through ACPICA's system-memory handler, and an address the
machine does not back is an external abort that kills the boot. The profile therefore backs every
block the vendor DSDT names, in one reusable RAM-backed device:

| block | who reads it | note |
| --- | --- | --- |
| `0x05170000` +0x60000 | `_CPC` registers, the power/performance interface | zero-backed |
| `0x1C238000` +0x1000 | `\_TZ.RREG(0x1C238818…0x1C238834)`, the thermal zones | seeded with tenths of a kelvin (45, 40, 50, 35, 55, 30, 60 °C) so a zone reports a plausible temperature |
| `0x1C870200` +0x400, `0x311C0000` +0x100 | declared by `\_SB.MTEL` | zero-backed |

`tests/test_declared_blocks.py` checks all of them, plus the console window, in two seconds.

The console needed the same treatment for a different reason: it is a **MediaTek 8250**, and
`mtk8250_set_termios` writes vendor registers past the eight a 16550 models — outside the model that
is an external abort in `mem32_serial_out`. The 16550 now sits inside a 4 KiB container with scratch
registers behind it, so a vendor register reads back present and inert.

## A closed lever: answering the runtime service as if it were the flash service

`efisubsys_init` stalls in a loop of FF-A direct requests. Traced from the monitor
(`firmware/runs/20261109T075617`, `--flash-trace`), the repeated call is
`FFA_MSG_SEND_DIRECT_REQ` — SMC function `0xC400006F` — with destination endpoint `3`, source endpoint
`0`, and message words `0xC4000064` and a buffer address (`a1=0x3`, `a3=0xc4000064`, `a4=0x93a7f000`),
**641 times** before the run ends. Two corrections to an earlier reading of that line: `0xC4000064` is a
**message word**, not the SMC function id, and endpoint `3` is not the eSPI/flash partition number
(`0x8003`/`0x8002`) — they are different numbering schemes. The trace's `ns` field reads `0`, so this call
is not demonstrated to be reachable from non-secure firmware, and it cannot be used as a known-good corpus
for a non-secure probe. It is also not the flash service renumbered — this monitor serves the
flash service at `0x8002` — and it is not an unknown endpoint either: it is the runtime half of the
same MM-communication path the repository already documents for the pre-runtime case. Accepting the `0x8004`
pair and answering it from the same `nor_request()` model looks like the obvious one-line fix, and it
is wrong: the firmware takes a synchronous exception immediately (`Synchronous Exception at
0x0000000084…`). Endpoint `0x8004` is a different service with a different protocol, not the flash
service under another number, and replying to it with flash semantics breaks the caller. The profile
therefore keeps `efi=noruntime` and the limitation is stated rather than papered over.

## What is still missing

The guest reaches the installer's **userspace** — `Run /init as init process`, then the installer's own
banner (`--- FastOS Version`, `VERSION="1.155.49"`), which `tests/test_profile_userspace.py` asserts. It
takes minutes of wall time under TCG, and a boot timeout of up to an hour is allowed for exactly that.
What is still missing is not the handover but the devices: the installation media and target disk are
not attached.

## A closed lever: entering the firmware at EL1

The firmware runs at EL2 and hands its payloads off at EL2, so the kernel inherits EL2. The obvious
shortcut was to enter the firmware at EL1 instead, the way a board without an EL2 stage would. It
does not work: with `SPSR_EL3 = 0x3c5` the firmware prints `MON: enter Spark UEFI` and stops — no
error, no further output, no setup. Entering at EL2 is what this firmware requires.

## Memory

A 3 GiB guest is the useful size (the profile aliases PCI space over the first gigabyte, so the guest
sees two). The harness refuses to start one without roughly 1.25× that in `MemAvailable`; on a host
under memory pressure the run is skipped rather than run into swap.

## Asking the guest a question

The vendor kernel's `/init` lives in an **embedded** initramfs with no shell on any path an injected
image can reach, so `init=/bin/sh` is ignored and a diagnostic script has nothing to interpret it. A
probe therefore has to be a static ELF that talks to the kernel directly: `tests/fixtures/guest_probe.c`
is one, built `-nostdlib -static` for `aarch64-linux-gnu`, raw syscalls only.

It is injected by appending it as `/init` to the vendor's `INITRD` and rebuilding the ESP: the kernel
unpacks the external initrd *after* the embedded one, so this file shadows the vendor init. Measured
consequences, both worth having in mind before using it:

- **it works.** The probe mounted `/proc`, `/sys` and `debugfs`, read `/proc/cmdline`, `/proc/iomem`
  and `/sys/...`, and printed all of it to the console;
- **it displaces the init that loads the modules.** With the probe as `/init`, no module is loaded, so
  the NVMe driver never probes and the `-EPROBE_DEFER` this was written to explain cannot occur. The
  deferred list is empty for that reason, not because nothing deferred;
- **there is nothing to hand control back to.** Measured in that boot: `/bin`, `/sbin` and `/usr/bin`
  do not exist (ENOENT), and `/` itself refuses `O_DIRECTORY` with EINVAL, so this rootfs has no shell
  on any path. The vendor "init" is a static binary that `/init` names, and an injected `/init`
  necessarily replaces it — there is no second init to `exec`.

So the `-517` cannot be reproduced by injecting an init unless that init also performs the vendor's
module loading. The modules are `.ko.zst` inside the same INITRD, which a freestanding probe cannot
decompress; the practical route is to decompress them on the host, place the `.ko` files in the
injected archive, and `finit_module` them from the probe — or to model the SMMU first, which is
independently the largest known difference between this profile and the machine.

What the same boot established about the platform, from `/proc/iomem`: three `arm-smmu-v3` instances
claim `0x13000000`, `0x13800000` and `0x14900000` — the addresses the vendor's IORT declares — while
the profile backs none of them. The SMMU is therefore a real device to the guest and an inert window
to the model, which is the largest remaining difference between this profile and the machine it
models.

## Why the NVMe driver defers, and what that costs

Measured with the injected probe and the modules loaded by hand from the archive:

- the device is put on the deferred list **with no supplier bound**, and nothing on the list explains
  it;
- ten seconds later the kernel prints `nvme 000f:00:02.0: deferred probe timeout, ignoring dependency`
  and the probe returns **0** — the driver binds and the namespaces appear;
- with `deferred_probe_timeout=1` on the command line, the vendor flow binds **both** controllers at
  9 s (`nvme nvme0: pci function 000f:00:02.0`, `nvme nvme1: …`) and `nvme0n1` appears.

So the `-517` is the driver's own probe, not a missing device behind it: the PCI function's sysfs
directory carries **no `supplier:*` links**, so it is not waiting on the SMMU — or on any other
device — and the kernel's `deferred_probe_timeout` is the only thing that eventually proceeds with the
probe anyway. Collapsing that timeout makes the boot deterministic.

Instrumentation is exhausted for this question, and that is worth writing down so nobody repeats it:

- `dyndbg="file drivers/nvme/host/* +p"` produces no output at all, and a global `dyndbg=+p` run has
  nothing between `really_probe: probing driver nvme` and `Added to deferred list` — the returning
  path carries no `dev_dbg`;
- the vendor kernel ships headers, not sources, so the call cannot be read out of a tree.

What is left is to bound the cost rather than to explain it: one second of delay, one documented flag,
and the boot is deterministic. A future attempt should start from the vendor's source for this kernel
version, not from another hypothesis.

Independently of that, the three SMMUv3 units the vendor's IORT declares at `0x13000000`,
`0x13800000` and `0x14900000` are still unmodelled — the guest claims their windows and the profile
backs them with an inert region — but they are no longer suspected as the cause of this deferral.

What that did **not** fix: with a disk present, the installer still stops at its `--- Command Line
Check` stage for the remainder of a 700-second run, so that stage is a separate blocker from the disk.

That conclusion was premature, and the correction is worth keeping because the wrong version was
plausible. The installer does not stall after the command-line check: without `noui` it execs
`/bin/dgx-spark-recovery`, a TUI on a console this profile does not provide, so it goes silent while it
is working. With `noui` it proceeds, and the next thing it wants is media, not a disk.

## The media is a `/dev/sd??`, and USB cannot supply one here

`init_mount` sleeps five seconds, then for up to ten attempts tries `/dev/sd??` as a partition, `/dev/sd?`
as a whole-disk ISO, and `/dev/sr0` as ISO, requiring `fastos-release.txt` at the mount root, and drops
to a shell if none of that works. Two facts decide how this machine can satisfy it:

- `init_prep` runs `modprobe xhci-plat-hcd` — the **platform** xHCI driver. The six `TYPE_XHCI_SYSBUS`
  controllers the profile creates are memory-mapped correctly, but no firmware node describes them, so
  no driver binds; the recovery initramfs also carries no generic `xhci_pci`. A `usb-storage` device is
  therefore unreachable, and pinning it to a controller bus cannot change that.
- the guest does drive **virtio-scsi**, which names its disk `/dev/sda`.

So the media is attached as a `scsi-hd` on a `virtio-scsi-pci` controller, and `--target` attaches the
install disk as `-device nvme` because `init_mount` hardcodes `/dev/nvme0n1`. Both are consequences of
the installer's own contract, not concessions: with them the run reaches

```
    - No UI
    - Skipping FW Update
-- Wait for USB Enumeration
Mounted /dev/sda1, checking for fastos-release.txt...
Found fastos-release.txt on /dev/sda1
-- Format Disk
nvme0n1: p1 p2
mkfs.fat 4.2 (2021-01-31)
```

and stops only because that media had no payload: `init_restore` mounts `/dev/nvme0n1p2`, which the
payload was supposed to fill, so `DATA_UUID` is empty and it exits on
`Error: NEWGUID environment variable not set`. `tools/make_recovery_key.py` builds the media the rest of
the flow needs from the vendor archive — `fastos-release.txt`, `efi.tar.xz`, `fastos.partaa`,
`fastos.partab` — into a partition on a `virtio-scsi` disk.

## The FF-A direct-request loop is the secure world's, not the kernel's

The loader passes `efi=noruntime` because an earlier note said the kernel's `efisubsys_init` spins in an
FF-A direct-request loop. With the monitor's relay now logging both sides of every direct request, and
the flag removed for one run, that premise does not hold:

- `efisubsys_init` **returns 0 in one millisecond** — no spin;
- the boot still reaches userspace (`--- FastOS Version`), so the flag is not what saves it;
- the requests that do loop carry `x1 = 3` — no modelled endpoint — and come from the **secure world**
  (`SMC … ns=0x0`, `elr=0x93a7e168`), in 640 identical pairs of which 466 are answered;
- EFI variables still do not work either way: the initramfs reports
  `mount: mounting none on /sys/firmware/efi/efivars failed: Operation not supported`.

So the flag is a no-op with a stale justification rather than a workaround. What actually gates EFI
variables is the firmware's runtime variable service, which is reached through an endpoint this
monitor does not serve — the same gap, now named precisely instead of hidden behind a flag.

## The installed disk boots to the installed kernel under the vendor UEFI

After `init_restore` completed (37 GB root, `e2fsck`, `resize2fs`, new UUID, fstab and `grub.cfg`
rewritten), the target was attached to the full-boot runner and the firmware booted it:

```
Version 2.22.1295. Copyright (C) 2026 AMI … Ver: 0ACUM028
[    0.000000] Booting Linux on physical CPU 0x0000000000 [0x000f0510]
[    0.000000] Linux version 7.0.0-1019-nvidia … 7.0.0-1019.19~24.04.2-nvidia
[    0.000000] earlycon: uart0 at MMIO32 0x0000000016a00000
[    0.000000] printk: legacy bootconsole [uart0] enabled
```

`7.0.0-1019` is the kernel inside the restored root; the recovery media carries `7.0.0-1018`. So the
chain ran: firmware → `EFI/BOOT/BOOTAA64.EFI` on `nvme0n1p1` → shim → GRUB → `/boot` on `nvme0n1p2` →
the installed kernel. No NVRAM entry was needed, which is why the earlier `efibootmgr` failure did not
block this: **the install is complete and the disk is bootable.**

Two things must not be read into that log:

- `boot_spark.py`'s verdict for that run was `setup/navigation not reached before the boot timeout`. That
  is the runner's firmware-UI check, and with a bootable disk the firmware never enters setup. It is not
  a statement about the kernel.
- GRUB prints nothing on this UART. Its console is the EFI text console, which is not modelled, so the
  gap between the firmware banner and the kernel line is expected.

## Why it goes silent after the bootconsole, and what is *not* established

The kernel stops printing immediately after `legacy bootconsole [uart0] enabled`, and stayed silent for
the remaining 57 minutes of a 3300-second run. The console model is not the explanation: at `0x16a00000`
the profile maps a real 16550 (`serial_mm_init`, regshift 2) inside a container, overlapped by a scratch
window so a vendor-register probe reads zero instead of aborting. Earlycon and the driver therefore talk
to the same controller.

The log names a suspect the kernel would hit as soon as it walks the EFI memory map:

```
[SsmpuDxe]: SsmpuDxeQueryTag 207 <#> Failed to find mem_info
[SsmpuDxe]: SsmpuDxeQueryTag 220 <#> Status=Invalid Parameter
[SsmpuDxe]: SsmpuDxeEntry 545 <#> Failed to Query MMAP_DISPLAY_FRM
```

A guest whose RAM starts at 1 GiB depends entirely on that map being right. Whether the silence is an
incomplete memory map, a hang in early memory init, or something later that stops writing to this
console is **not established** — the two candidates are named so the next probe starts from evidence
rather than from a guess. The next probe is a run with `nokaslr ignore_loglevel earlycon keep_bootcon`
and, if it stays silent, monitor-side tracing of the first post-console accesses.

## The installed disk can be inspected from inside the emulator

No host tool here reads ext4 at a partition offset (`debugfs` has no offset option and `fuse2fs` is not
installed), so the emulator reads its own install: `run_linux.py` now takes `--console-script`,
`--console-after` and `--console-delay` and types the file's lines into the guest, which with `-nographic`
arrives on the shell that `usb.shell` starts before `init_mount`. Measured from that shell:

```
# mount /dev/nvme0n1p2 /mnt/root
EXT4-fs (nvme0n1p2): mounted filesystem 69909700-2b7b-41a4-a817-5d87b823349f r/w
# ls /mnt/root/boot
config-7.0.0-1019-nvidia   initrd.img                    system.map-7.0.0-1019-nvidia
initrd.img-7.0.0-1019-nvidia   vmlinuz                   vmlinuz-7.0.0-1019-nvidia
# blkid
/dev/nvme0n1p1: LABEL="EFI"  UUID="D4C3-27CF"      TYPE="vfat"
/dev/nvme0n1p2: LABEL="root" UUID="69909700-…349f" TYPE="ext4"
```

The writer is a background thread that must not close the stream: `Popen.communicate()` flushes it
afterwards, and closing it made that flush raise `flush of closed file`, which replaced the run's output
and threw away the capture. `tests/test_linux_boot.ConsoleScriptTests` pins both properties — one line
typed once, stream left open, and a closed stream treated as the guest exiting rather than an error.

## Boot arguments decide how much the installed kernel can say

The same disk booted with the line the installer wrote stops after `printk: legacy bootconsole [uart0]
enabled` and says nothing for the rest of an hour. With the ESP's `grub.cfg` replaced by an explicit
stanza — `keep_bootcon`, `earlycon=uart,mmio32,0x16a00000`, `loglevel=8 ignore_loglevel`, `nokaslr`,
`console=ttyS0,115200 console=ttyAMA0` — the same kernel now prints past the bootconsole and enumerates
its own storage:

```
pci 000f:00:02.0: [1b36:0010] type 00 class 0x010802 PCIe Root Complex Integrated Endpoint
pci 000f:00:02.0: BAR 0 [mem 0x24000000-0x24003fff 64bit]
acpi PNP0A08:0b: ECAM at [mem 0x29000000-0x291fffff] for [bus 00-01]
```

Before the context-switch fix below, it reached `scmi_core: SCMI protocol bus registered` and stopped.
The recovery boot's later `Run /init as init process` did **not** establish that the installed boot was
merely slow. The earlier claim that another hour would resolve this gap was wrong: the installed
kernel triggers a secure-world execute-permission fault, followed by recursive vector-fetch faults.

The original ESP stanza is preserved as `EFI/ubuntu/grub.cfg.orig` on the disk, so the verbose line is a
diagnostic that can be reverted from the same shell that wrote it.

## The FF-A register file: an initial return-address hypothesis, since disproved

QMP took 69 seconds to answer in one measurement; that delay alone does not identify a guest loop.
The original dump at the stall was:

```
PC=0x0000000000000200  X00=0x00000000c400006f  X01=0x0000000000008004
X29=0x0000000093a7bce0 X30=0x0000000093a779d8  SP=0x0000000093a7bce0
PSTATE=0x00000000000023c5 ---- EL1h
```

`x0`/`x1` identify the FF-A request to STMM, and `PC=0x200` identifies the synchronous current-EL/SPx
vector with a zero base. They do **not** establish that the context is Linux, or that the monitor
returned to the wrong address. The decisive exception trace below shows a correct return into STMM
whose instruction fetch is denied. The complete later register dump explicitly says `S EL1h`.

Likewise, a zero `VBAR_EL1` can belong to an EL2 firmware caller, or to this STMM partition, which
has no EL1 vector base at its first yield. AArch64 `VBAR_EL1` is not security-banked
in this QEMU; its per-world save/restore is necessary, not evidence of corruption by itself.

An early sampler attempt also exhausted its output before the kernel: its addresses were all firmware,
`0xbfab1254` alone accounting for 36 094 of 81 496 samples. That did not make the plugin unusable;
the later start/limit corrections below made it useful.

## A rejected loop diagnosis, and the sampler fixes that exposed its limit

The sampler's `limit` counted samples from reset, so the firmware phase (about 2.1e10 blocks, measured:
105 603 samples at a 200 000 interval) exhausted a 4 000-sample limit and censored every kernel sample.
With the limit raised, the same sampler captured this concentration:

```
     24 pc=0xffff8000803518b8   update_event_printk+0x50
     22 pc=0xffff8000803518c0   update_event_printk+0x58
     20 pc=0xffff8000803518b0   update_event_printk+0x48
     18 pc=0xffff800080351930   update_event_printk+0xc8
     12 pc=0xffff8000803518d8   update_event_printk+0x70
```

Resolved against the installed kernel's `System.map-7.0.0-1019-nvidia`, 96 samples sit inside 128 bytes
of `update_event_printk`. This shows a print-format scan executed, not that it was infinite. The earlier
claims of an idle wait and then a persistent loop both overinterpreted a censored sample distribution.

`--trace-start N` was added so a sampler can skip a firmware phase whose block count a previous run's
summary reports, rather than depending on a limit large enough for both phases.

The disassembly below explains those sampled instructions; the repeated-count measurement and exception
trace establish why they were not the cause of the stall.

### What the loop is made of

The installed kernel image is on the host (`firmware/state/symbols/Image.bin`, decompressed from the
`/boot/vmlinuz-7.0.0-1019-nvidia` the guest copied to the ESP), so the sampled offsets can be read
directly: slice the bytes, wrap them in an ELF with `aarch64-linux-gnu-objcopy -I binary`, and
disassemble with the virtual address supplied by `--adjust-vma`.

```
18b0: cmp  w3, #0x5c           ; '\\'
18b8: cmp  w3, #0x22           ; '"'
18c0: cbnz w23, +0x90
18d8: ldrb w0, [x20, w3, sxtw] ; character-class table at 0xffff800081ee90c0
18dc: cmp  w3, #0x5f           ; '_'
```

These are the comparisons and the class lookup of a print-format scan: backslash, quote, table. The
sampled offsets are the body of that scan, not a call site.

At that point an unterminated format string and slow but finite scanning both fitted the samples.
Neither was established. Comparing the **total sample count**, not just its offset distribution,
showed below that no further translated block was executing at all.

### No new translated blocks does not mean an interrupt wait

The same sampler run, measured ten minutes apart, had **10 062 samples before and 10 062 after**, with
the harness and QEMU still alive. This rules out the proposed indefinitely retiring format-string loop.
It does **not** establish WFI, an idle vCPU, or a missing interrupt: repeated faults fetching an unmapped
exception vector also produce no translated blocks. The earlier interrupt-wait claim is withdrawn.

The harness holds its own QMP client, so a second client can be refused (`ConnectionResetError`).
`tools/replay_and_probe.py` bypasses that ownership issue by replaying the recorded argv with an
unowned socket. The later context probes likewise used one direct-launch QMP owner.


## The decisive exception trace: secure permission-indirection state leaked across worlds

The first `--trace-exceptions` (`-d int`) run, `20261111T154523.547381Z`, preserved the first exception
entering `0x200` in `first-vector-fault.log`, with the full trace in `qemu.log` and the last kernel line
in `serial.log` (`scmi_core`, kernel time 5.401990 s):

```text
NS EL2 SMC: ELR=0x87991f08
EL3 -> EL2 dispatcher -> EL1: PC=0x93a7e168
Prefetch Abort: EL1 -> EL1, ESR=0x8600000f, FAR=ELR=0x93a7e168
...to EL1 PC 0x200 PSTATE 0x23c5
Prefetch Abort: EL1 -> EL1, ESR=0x86000005, FAR=ELR=0x200
```

`EC=0x21`, `IFSC=0x0f` is an instruction permission fault at level 3, at STMM's correct resume address.
Only the subsequent `IFSC=0x05` faults are level-1 translation faults fetching the unmapped vector.
It is not an SCMI handshake, an idle wait, or a corrupted return address.

The instrumented direct replay, `20261111T155336.769648Z-context-before`, preserved `registers.log`,
`first-vector-fault.log`, `measurement.json`, `serial.log`, the monitor binary and command/hashes:

- QMP reports `running: true`; the stopped register file explicitly reports **`S EL1h`**, PC `0x200`,
  SP/FP `0x93a7bce0`, LR `0x93a779d8`. The faulting context is **secure STMM**, not Linux.
- At its **first secure save**, before UEFI ran, `VBAR_EL1=0`, `TCR2_EL1=0`, `TCR2_EL2=0`.
  The monitor later restores that saved zero vector base correctly. This establishes the value at
  yield, not a complete earlier history of vector-register writes. No evidence implicates Linux's
  vector base being zeroed by the monitor.
- At the post-`scmi_core` request, NS `VBAR_EL1=0xffff800080011000`, both `TCR2` registers are `2`
  (`PIE=1`), and `HCRX_EL2=0xc04800`. After the secure restore, its legacy TTBR/TCR/SCTLR and zero
  vector base are restored, **but both live `TCR2` values remain `2`**. The old monitor omitted them.
- This QEMU registers AArch64 `TCR2_EL1/EL2`, `PIR_EL1/EL2`, `PIRE0_EL1/EL2` and `HCRX_EL2` with
  one storage location each, not security-state banks. SCR feature enables permit accesses but do not
  switch this state. Linux's indirect permissions were being applied to STMM's legacy descriptors.

`monitor.c` now saves/restores that stage-1 permission-indirection group and `HCRX_EL2` per world.
It discovers optional register support once from `ID_AA64MMFR3_EL1` and `ID_AA64MMFR1_EL1`, restores
the controls/tables while the MMUs are off, and leaves SCR's feature enables unchanged. This restores
the secure values rather than masking Linux's features. The supported profile uses `-cpu max`, where
MMFR3 itself exists; this is not a portable probe for arbitrary older cores. `--flash-trace` logs
saved/live vector and translation controls at each save/restore.

The existing synthetic world-switch test now identity-maps executable secure RAM using legacy 4 KiB
pages, enables NS PIE in both regimes with a no-access permission index, and verifies NS
TCR2/PIR/PIRE0/HCRX state after two round trips. The final fixture reproduces the **same `0x8600000f`
level-3 instruction permission fault** against the pre-fix monitor and passes against the fixed one
(`regression-permission-trace.log`, `regression-before-final.log` and `regression-after.log` beside
the context-before capture). Test coverage is executable, not a source-text assertion.

### The fixed installed disk reaches its own systemd

`20261111T160507.692000Z-context-fixed` replays the same installed-disk input, QEMU and unchanged
vendor images with a fixed traced monitor, 2 GiB allocation and the unchanged 512 MiB admission
reserve. Its `serial.log` records:

```text
[    2.784314] scmi_core: SCMI protocol bus registered
[   18.422226] Run /init as init process
[   38.672726] EXT4-fs (nvme0n1p2): mounted filesystem ... ro with ordered data mode.
[   45.105066] systemd[1]: systemd 255.4-1ubuntu8.12 running in system mode
Welcome to Ubuntu 24.04.4 LTS!
[   55.183342] EXT4-fs (nvme0n1p2): re-mounted ... r/w.
```

`/init` was observed after 80.3 seconds of QEMU wall time; capture continued another 180 seconds.
The post-kernel relay trace shows secure `TCR2_EL1/EL2=0`, PIR/PIRE0/HCRX all zero, followed by
NS `TCR2_EL1/EL2=2`, `PIR_EL1/EL2=0xcc880e0ac0800000`,
`PIRE0_EL1/EL2=0x5010908070320000`, `HCRX_EL2=0xc04800`. The NS vector base remains nonzero.
Secure STMM now answers the request and Linux proceeds past the former fault. `measurement.json`
indexes those lines; `registers.log` at the end reports Linux running in **NS EL2h**, not `0x200`.

This establishes the installed-root boot through initramfs, root mount and systemd, not a completed
desktop or login. At the end of the bounded capture, AppArmor was still loading profiles and
`nvidia-remove-gnome-software` had a startup job in progress; neither is established as another stall.
No login prompt was observed. GCS and other optional register-context groups have not been
independently exercised; this change fixes the measured permission-indirection leak, not every
possible extension-state leak. GPU, physical NIC firmware and production isolation remain outside
the profile's claim. The recovery boot was not repeated for this change.

Final verification for this change: `python3 -m unittest discover -s tests -q` ran **413 tests,
51 skipped, OK**. With `SPARK_QEMU` set, all three monitor integration tests passed, including the
two-CPU start and memory-sharing tests as well as the strengthened permission-context test. Their
output is preserved as `unittest.log` and `monitor-integration-tests.log` in the fixed run.

