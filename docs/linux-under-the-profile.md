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

