# Booting vendor Linux under the Spark profile

This page preserves the investigation from recovery-kernel handoff failures
through installer completion and an installed-root serial login under the
vendor UEFI and this repository's monitor. See the final sections for the
measured boundary; earlier failed runs remain historical evidence.

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
- **`_CPC` is read at boot, so the block that holds it has to exist.** The firmware's ACPI tables place
  it in a block at `0x05170000` (`0x60000` bytes), and ACPICA's system-memory handler faults on any address
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
| `0x1C238000` +0x1000 | the thermal zones | seeded with tenths of a kelvin (45, 40, 50, 35, 55, 30, 60 °C) so a zone reports a plausible temperature |
| `0x1C870200` +0x400, `0x311C0000` +0x100 | other declared blocks | zero-backed |

`tests/test_declared_blocks.py` checks all of them, plus the console window, in two seconds.

The console needed the same treatment for a different reason: it is a **MediaTek 8250**, and
`mtk8250_set_termios` writes vendor registers past the eight a 16550 models — outside the model that
is an external abort in `mem32_serial_out`. The 16550 now sits inside a 4 KiB container with scratch
registers behind it, so a vendor register reads back present and inert.

## A closed lever: answering the runtime service as if it were the flash service

An early investigation attributed a stall to `efisubsys_init`; the later
relay capture below disproved that attribution. Traced from the monitor
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
service under another number, and replying to it with flash semantics breaks the caller.
At that historical stage the profile kept `efi=noruntime`. The subsequent
relay/context correction uses real STMM, not a flash-service substitution;
the installed guest now has runtime variable access as measured below.

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

## Historical recovery-media variable failure, before the context fix

At this stage the loader passed `efi=noruntime` because an earlier note
said the kernel's `efisubsys_init` spins in an FF-A direct-request loop.
With the monitor's relay logging both sides of every direct request,
and the flag removed for one run, that premise did not hold:

- `efisubsys_init` **returns 0 in one millisecond** — no spin;
- the boot still reaches userspace (`--- FastOS Version`), so the flag is not what saves it;
- the requests that do loop carry `x1 = 3` — no modelled endpoint — and come from the **secure world**
  (`SMC … ns=0x0`, `elr=0x93a7e168`), in 640 identical pairs of which 466 are answered;
- EFI variables still did not work in that **pre-fix recovery-media run**:
  `mount: mounting none on /sys/firmware/efi/efivars failed: Operation not supported`.

The flag was a no-op with a stale justification rather than a workaround.
The monitor already relayed endpoint `0x8004` to real STMM; it was wrong
to describe that endpoint as an unimplemented service. The later measured
fault was permission-indirection state crossing the world boundary, fixed
by the per-world TCR2/PIR/PIRE0/HCRX save/restore.

**Present measured state:** `20261112T060832.086295Z/serial.log` records
`EFIVARFS_MOUNT_RC=0`, `efivarfs efivarfs /sys/firmware/efi/efivars`,
and `EFIBOOTMGR_CREATE_RC=0` for `Boot0006* Spark runtime persistence`,
whose loader is `\EFI\ubuntu\shimaa64.efi`. A separate-process cold boot,
`20261112T063956.565964Z/serial.log`, reaches `localhost login: root`
and reports `BootCurrent: 0006` with that same entry and loader.
The matching creation-after/cold-before NOR hashes and complete
[create-and-cold-boot evidence](runtime-variables.md#fresh-runtime-create-and-cold-boot-persistence)
establish persistence, not just a successful create in memory. The
earlier installer failure and fallback-only boot are historical, not a
remaining runtime-service gap.

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

`7.0.0-1019` is the kernel inside the restored root; the recovery media carries `7.0.0-1018`.
That historical first boot used firmware → `EFI/BOOT/BOOTAA64.EFI` on
`nvme0n1p1` → shim → GRUB → `/boot` on `nvme0n1p2` → the installed kernel.
No NVRAM entry was needed then, so the earlier installer `efibootmgr`
failure did not block it: **the install completed and the disk was bootable**.
This is not a present restriction to fallback boot. An NVRAM boot entry
can also name a file in `EFI/BOOT`; only `BootCurrent` plus the entry's
device/file path distinguishes that selection from removable-media
fallback. See the [runtime persistence evidence](runtime-variables.md#fresh-runtime-create-and-cold-boot-persistence).

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
one address alone accounting for 36 094 of 81 496 samples. That did not make the plugin unusable;
the later start/limit corrections below made it useful.

## A rejected loop diagnosis, and the sampler fixes that exposed its limit

The sampler's `limit` counted samples from reset, so the firmware phase (about 2.1e10 blocks, measured:
105 603 samples at a 200 000 interval) exhausted a 4 000-sample limit and censored every kernel sample.
With the limit raised, the same sampler concentrated 96 samples inside 128 bytes of the installed
kernel's `update_event_printk`, resolved against its symbol map. This shows a print-format scan
executed, not that it was infinite. The earlier claims of an idle wait and then a persistent loop
both overinterpreted a censored sample distribution.

`--trace-start N` was added so a sampler can skip a firmware phase whose block count a previous run's
summary reports, rather than depending on a limit large enough for both phases.

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

## Installed-root serial login and packet-fan test setup, 2026-11-11

The installed root initially had `getty@tty1`, but no enabled serial getty.
The local test disk now contains
`/etc/systemd/system/getty.target.wants/serial-getty@ttyS0.service`, a symlink
to `/lib/systemd/system/serial-getty@.service`. Its ESP stanza uses
`console=ttyS0,115200 earlycon=uart,mmio32,0x16a00000
systemd.unit=getty.target nokaslr`, retaining the installed kernel, initrd and
root UUID. `getty.target` is an explicit bounded login-test target, not a
claim that the full vendor multi-user startup or desktop has completed.
AppArmor finishes before the serial getty starts; its earlier long-running
startup was not an established stall. The installer's ESP stanza remains
beside the test stanza as `grub.cfg.orig`.

For an authenticated test shell, only the root password field in this
throwaway installed image's `/etc/shadow` was replaced with a fresh SHA-512
hash generated by `openssl passwd -6`; the original shadow file is retained
locally with mode 0600. No factory credential was guessed, no autologin or
password is shipped, and no host authentication state was changed. This
disk is an isolated, regenerable installer output, not a distributable OS
image. Restore its original shadow file (or regenerate the disk) before any
non-lab use. The QEMU run has `-nic none`.

`boot_spark.py --ffa-ec` enables the monitor packet service; the option is
off by default. The packet-path run below uses `--firmware-version 2.155.11`.

### One installed-guest run reaches login and controls the modelled fan

The retained run `20261111T175332.261188Z` uses unchanged vendor UEFI and
STMM **2.155.11**, the installer-produced NVMe image, 2 GiB allocation,
the unchanged 512 MiB admission reserve, and this command:

```sh
python3 tools/boot_spark.py --firmware-version 2.155.11 --memory 2G \
    --timeout 3600 --nvme-image firmware/state/install-target-40g.img \
    --rot-functional --no-setup-escape --flash-trace --ffa-ec
```

Its `serial.log` contains the actual `localhost login:` prompt, a password
login, and guest commands showing kernel `7.0.0-1019-nvidia`, page size
`4096`, and `serial-getty@ttyS0.service` both `enabled` and `active`.

Linux enables SMCCC **1.2** (the monitor's exact version is `0x00010002`).
The FF-A driver performs count-only and RX-buffer partition discovery;
`arm-ffa-1` exposes UUID `78b04d80-d21d-4986-8acb-467b60247ac5` and
partition ID `0x8003`.

`MONITOR_TRACE` records the guest's write packet `0x0000119400020501` and
floor `0x1194`; read-floor operation 4 and telemetry operation 7 follow it.
Automatic restoration sends `0x0000ffff00020501` and returns the floor to
`0xffff`. Capabilities operation 1 and completion poll command 2 are
exercised during probe and these operations. These are model results, not
tachometer or thermal measurements.

The run directory keeps `serial.log` and `run.json`. The harness stopped
normally, with no orphaned guest. The test does not establish full vendor
multi-user startup, a desktop, physical EC timing, thermal response, GPU/NIC
operation, or a complete FF-A implementation.

Final verification: `python3 -m unittest discover -s tests -q` ran
**413 tests, 51 skipped, OK**. With `SPARK_QEMU` set to the patched QEMU,
the three monitor integration tests also passed, including default-build
SMCCC/version and context preservation, two-CPU start, and the EC-enabled
partition/RX-ownership/native-packet plus existing memory-share lifecycle.
Host fan-model cases exercise both direct-request layouts, capability
encoding, floor readback, saturation, automatic restore and rejected
operation/length/UUID/endpoint combinations. The run retains
`unittest.log` and `monitor-integration-tests.log`.

## Default-target boot and native ACPI selection

Retained run **`20261111T191435.858956Z`** boots the installed NVMe image with
unchanged UEFI/STMM **2.158.15**, `--ffa-ec`, `--rot-functional`,
`--no-setup-escape`, `--flash-trace`, and 2 GiB allocation. The ESP stanza
now has **no `systemd.unit=` override**. Its kernel command line in
`serial.log` is:

```text
BOOT_IMAGE=/boot/vmlinuz-7.0.0-1019-nvidia root=UUID=69909700-2b7b-41a4-a817-5d87b823349f ro console=ttyS0,115200 earlycon=uart,mmio32,0x16a00000 nokaslr
```

The same capture has the actual `localhost login:` prompt and, after
authenticated login, these guest journal/status lines:

```text
Reached target multi-user.target - Multi-User System.
Reached target graphical.target - Graphical Interface.
MULTI_USER_ACTIVE=active
DEFAULT_TARGET=graphical.target
```

The target messages were retrieved with
`journalctl -b --no-pager -o cat -g 'Reached target (multi-user|graphical).target'`,
not fabricated with `echo` or reached by manually starting a different target.
First-boot snap seeding and Docker startup took time; the natural default
transaction finished without masking, stopping, or replacing services. This
is **not a healthy-desktop claim**: DGX OOBE, CPU-governor and NVIDIA CDI
services fail on the incomplete hardware/network model, and snap seeding
reports unavailable online prerequisites even though its script exits 0.

Under 2.158.15 the kernel's own `acpi-fan` driver binds both `PNP0C0B`
platform devices; their `thermal_cooling` links resolve to
`/sys/devices/virtual/thermal/cooling_device0` and `cooling_device1`, each
with type `Fan`. No `_FSL` writes were performed: this is driver binding,
not physical cooling.

## Lab platform Secure Boot provisioning

The lab retains two independent, ignored NOR stores:

| State | Local path | Meaning |
| --- | --- | --- |
| Secure Boot off | `firmware/state/spark-secureboot-off.bin` | The earlier Secure-Boot-off store, copied byte for byte before provisioning; SHA-256 `f137c0e4e0a0577b5ac42d055c9b84b925e1c4fb4c04ce251fbddd97322caa19` |
| Secure Boot on | `firmware/state/spark-secureboot-on.bin` | Independently enrolled PK, KEK and db |

These are the **1 MiB legacy variable-store inputs** used by the preceding
installed-guest experiments, not a manufacturer's complete 64 MiB flash
part. Select the off copy explicitly when reproducing earlier results; do
not reuse the on store and assume its security policy is unchanged.

### Exactly which certificates were supplied

All hashes below are SHA-256 of the public DER certificate, not the
certificate's public-key identifier or an EFI signature-list hash.

| Trust location | Certificate and source | Purpose |
| --- | --- | --- |
| PK | `DGX Spark emulator lab PK`; newly generated self-signed RSA-2048/SHA-256 lab certificate, `216b025e3a83048b8ccdd0bf27d2e6771990a27acb4edcdf6ee6971a47ffcd2b` | Lab platform owner; authorizes PK/KEK variable updates |
| KEK | `DGX Spark emulator lab KEK`; a separate newly generated self-signed RSA-2048/SHA-256 lab certificate, `92074826cb9b9c32ad525340bb29bcbb13776660ce3ba11354c4c74e04d74ff7` | Authorizes image-allowlist updates; the KEK variable update was signed with the lab PK |
| db | **Only** `Microsoft Corporation UEFI CA 2011`, `48e99b991f57fc52f76149599bff0a58c47154229b9f8d603ac40d3500248507` | Authenticates the ESP's Microsoft-signed shim; extracted unchanged from that shim's embedded Authenticode certificate chain, not generated or re-signed locally |
| Shim's embedded vendor trust, **not db** | `Canonical Ltd. Master Certificate Authority`, `ed1fe72cb9ca31c9af5b757afcd733323d675825032e6ced7fe1ae9eb767998c`; already embedded in the unchanged Ubuntu shim | Authenticates the Canonical-signed GRUB and Ubuntu kernel chain |

The X.509 PK and KEK certificates are independently self-signed.
The **authenticated variable updates**, rather than an invented X.509
issuer chain between those certificates, establish the UEFI hierarchy.
The lab PK/KEK private keys remain in the ignored, mode-0600 local
`firmware/state/secureboot-lab/` directory. They are not production keys
and are never committed.

The actual ESP shim (`EFI/BOOT/BOOTAA64.EFI`, identical to
`EFI/ubuntu/shimaa64.efi`) has SHA-256
`706f15b9578f780a2fddda8ee0806cd15b59124692cc297db320414a5a40fe44`.
Its leaf is `Microsoft Windows UEFI Driver Publisher`, issued by the
Microsoft UEFI CA above. **GRUB is not Microsoft-signed**:
`EFI/ubuntu/grubaa64.efi`, SHA-256
`b9d9dd81845d68a1f1867b5bcf26393908a9a349e749ce8ea944a1066d9abfcc`,
is signed by `Canonical Ltd. Secure Boot Signing (2022 v1)`, issued by the
Canonical Master CA. Host `sbverify --list` identified both chains;
`sbverify --cert` with the corresponding public PEM CA reported
`Signature verification OK` for each image. No Canonical certificate was
added to db to make a failing GRUB pass.

### Provisioning path and custody

Retained run **`20261112T015108.967843Z`** provisioned through the unchanged
firmware's actual runtime `SetVariable` service, using guest efivarfs.
The same session records `ENROLL_RC=2` before the successful `ENROLL_RC=0` retry; the outputs below describe that retry.
The guest started in `SetupMode=1`. Each certificate was wrapped in a
standard `EFI_CERT_X509_GUID` signature list, owner GUID
`604dab50-e046-4300-abb6-3dd810dd8b23`. Updates used attributes `0x27`
(nonvolatile, boot-service, runtime, time-based authenticated write).
The signed content was the UTF-16LE variable name without its terminator,
vendor GUID, attributes, EFI timestamp and signature list. OpenSSL CMS
produced detached binary SHA-256 PKCS#7 signatures with no signed
attributes; an `EFI_VARIABLE_AUTHENTICATION_2` header carried each
signature and timestamp.

Enrollment order was **db signed by KEK → KEK signed by PK → PK signed by
itself**, installing PK last while the firmware was in Setup Mode.
The guest refused to replace an existing key variable and read the
resulting variables back through efivarfs. The capture records:

```text
SETUP_MODE_BEFORE=01
ENROLLED_db_BYTES=2881
db_ATTRIBUTES=0x27
db_ESL_SHA256=79bba62f0edc0c0fb6fe7d8476d5a598a7d45c4e391ce213d15e8e265ce2e8d1
ENROLLED_KEK_BYTES=2139
KEK_ATTRIBUTES=0x27
KEK_ESL_SHA256=648c8c8311d721d83e921ddbac567ddd71c64fec08b9f22ea9efb1e9e134b6de
ENROLLED_PK_BYTES=2137
PK_ATTRIBUTES=0x27
PK_ESL_SHA256=b2c74af813fde95aa25610ecaae78aabc060a163c83a2534a885d900c435cb6e
SetupMode=0600000000
SecureBoot=0600000000
ENROLL_RC=0
```

The first four bytes of those final two values are efivarfs attributes
`0x6`; their data byte is zero. Installing PK leaves Setup Mode
immediately, but Secure Boot becomes effective on the **next cold boot**.
The existing `SecureBootSetup` policy already requested enablement; no
read-only `SecureBoot` value was forged and no NOR variable was
hand-edited. Stopping the harness saved the live firmware-written store.
The vendor UEFI/STMM, shim, GRUB, kernel, monitor and QEMU were not patched
for this enrollment.

For authenticated lab access, the isolated installed disk's current
`/etc/shadow` was preserved as mode-0600
`firmware/state/fan-lab/shadow.pre-sb` alongside the original/test copies,
then only its root password hash was replaced with a fresh
`openssl passwd -6` hash. Failed serial-login timing attempts used a
credential that was subsequently rotated; the later credential is not
stored in retained artifacts or published. No host credential was
changed. This remains a disposable disk: restore its original shadow or
regenerate it before non-lab use.

### Cold-boot chain and kernel trust

Retained run **`20261112T022953.618760Z`** cold-boots the installed image
under unchanged vendor UEFI/STMM **2.155.11**:

```sh
python3 tools/boot_spark.py --firmware-version 2.155.11 --memory 2G \
    --timeout 3600 --state-file firmware/state/spark-secureboot-on.bin \
    --nvme-image firmware/state/install-target-40g.img \
    --rot-functional --no-setup-escape --ffa-ec
```

The exact requested fallback path is exercised, not inferred from an
identical binary elsewhere: guest `efibootmgr -v` reports
`BootCurrent: 0000` and
`Boot0000* Spark shim fallback ... /File(\EFI\BOOT\BOOTAA64.EFI)`.
That boot entry was created with the guest's normal `efibootmgr --create
--disk /dev/nvme0n1 --part 1 --label 'Spark shim fallback' --loader
'\EFI\BOOT\BOOTAA64.EFI'`. The fallback directory originally lacked GRUB;
the existing, **byte-identical** `EFI/ubuntu/grubaa64.efi` and the existing
`grub.cfg` were copied beside its shim. Neither original binary nor the
existing `EFI/ubuntu` path was changed. The pre-copy ESP is retained as
`firmware/state/secureboot-lab/esp-before-fallback.img`.

The chain is therefore:

```text
vendor UEFI -- db Microsoft UEFI CA 2011 --> EFI/BOOT/BOOTAA64.EFI (shim)
shim -- embedded Canonical Master CA --> Canonical-2022-signed GRUB
GRUB/shim verification -- Canonical Master CA --> Canonical-2022-signed kernel
```

The installed kernel is gzip-compressed. After decompression, its original
ARM64 EFI Image has SHA-256
`6220c8a5fca3340de6e0b3b7da5c834d1ff2868c90f2eeb171695ec393df45c6`;
the installed gzip file has SHA-256
`ddd5a25c2326fbfdf7e03809c614fafe5a953416bbb1b80f9c2258f7260e44e3`.
Host `sbverify` identifies the same Canonical 2022 v1 signer as GRUB and
verifies it against shim's embedded Canonical Master CA. The run retains
`host-signature-verification.json` and `enrolled-key-hierarchy.json`.
`secureboot-evidence.json` indexes actual positive command results and
the separate component refusals by ANSI-stripped serial-log line number;
the original logs are unchanged.

The gzip wrapper starts `1f 8b`; that does **not** mean the decompressed
kernel lacks an EFI stub or signature. Verification concerns the
decompressed PE/COFF Image, which starts `MZ`. GRUB's real Secure Boot
path successfully loads this original compressed kernel; the deliberately
corrupted compressed-kernel control below fails at that same verification
step. No new kernel, signature or PE stub was built for the positive run.

Guest evidence, independently of the host's certificate inspection:

```text
secureboot: Secure boot enabled
Kernel is locked down from EFI Secure Boot mode; see man kernel_lockdown.7
localhost login: root
ENFORCED_ROOT_AUTHENTICATED=0
SecureBoot enabled
LOCKDOWN=none [integrity] confidentiality
EFIVAR_SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c=0600000001
EFIVAR_SetupMode-8be4df61-93ca-11d2-aa0d-00e098032b8c=0600000000
```

`ENFORCED_ROOT_AUTHENTICATED=0` is the output of `id -u`, not a fabricated
authentication verdict. The efivarfs values include their four-byte
attributes; their data bytes are respectively **1** and **0**. The guest
also reads PK, KEK and db back with attributes `0x27` and certificate
SHA-256 values exactly matching the table above. In particular, db has
**only the Microsoft certificate**; the Canonical certificate was not
added to db.

The same retained capture ends with the unmodified default transaction
complete, not merely with getty running ahead of it:

```text
MULTI_USER_ACTIVE=active
DEFAULT_TARGET=graphical.target
Reached target multi-user.target - Multi-User System.
Reached target graphical.target - Graphical Interface.
```

The target lines were retrieved from the guest's actual boot journal.
No service was masked, stopped or replaced to finish the transaction,
and the kernel command line has no `systemd.unit=` or `init=` override.
The slow first-boot desktop/snap/Docker transaction was allowed to finish.

### Secure Boot provenance boundary

The keys we control are the newly generated, separate **lab PK and lab KEK**.
Their public-certificate hashes and distinct roles are in
[the certificate inventory](#exactly-which-certificates-were-supplied).
The Microsoft and Canonical CAs are unchanged third-party certificates,
not locally generated keys: **db contains only Microsoft Corporation UEFI
CA 2011**; Canonical trust is embedded in shim.

Retained `20261112T022953.618760Z/serial.log` records
`DBX_PRESENT=False`, alongside the PK/KEK/db readback and Secure Boot
enablement. There is **no dbx**, so the image-signature checks do not
demonstrate current revocation coverage. The vendor firmware itself is
**not authenticated by the synthetic loader or functional ROT**; its
original-image hashes are provenance checks, not a modelled hardware
root of trust.

This is **not a manufacturer's provisioned Spark root of trust**. The
owner supplied both lab PK and lab KEK and manually installed the
authentic Microsoft image CA. A manufacturer's platform-owner PK,
OEM/operating-system update KEKs, image allowlist and maintained revocation
policy were not imported from a unit; their exact per-unit inventory
remains unmeasured. Guest readback reports **`DBX_PRESENT=False`**: this
is signature enforcement with a minimal allowlist, **not current dbx
revocation coverage** or Microsoft-2023-CA transition validation.

The unchanged shim supplies its own Canonical vendor trust and existing
SBAT policy; no production-key rotation, revocation update, SBAT-revoked
image, firmware capsule update or authenticated-variable replay/rollback
suite is claimed. The synthetic loader and `--rot-functional` replies do
not authenticate the executing vendor firmware. This result starts at
that firmware's genuine UEFI image-verification machinery, not at a
verified silicon boot ROM/BL31 chain or a TPM measured-boot boundary.
Finally, the external initramfs, GRUB configuration, kernel command line
and installed root filesystem are not covered by this signed-kernel
experiment: it is not an immutable or fully authenticated operating-system
image.

### Independent negative controls for all three image-verification steps

Three short boots use **separate negative-control FAT disks and separate
copies of the enrolled NOR**. They do not alter the installed disk, the
accepted on store, the retained off store or any positive-run boot binary.
Each control retains the original image's Authenticode signature while
flipping one covered payload byte: shim `.sbat`, GRUB `.sbat`, or kernel
`.text` respectively. For the kernel control, the tampered Image is
recompressed as valid gzip. Only these explicitly named negative-control
artifacts are modified.

| Verification boundary | Covered byte flipped | Component's actual refusal | Retained run |
| --- | --- | --- | --- |
| Firmware → shim | Shim `.sbat` + 10 | Firmware dialog `Secure Boot Violation`: `Invalid signature detected. Check Secure Boot Policy in Setup` | `20261112T024939.880448Z` |
| Shim → GRUB | GRUB `.sbat` + 10 | Shim `Verification failed: (0x1A) Security Violation` | `20261112T025057.710804Z` |
| GRUB/shim → kernel | Decompressed kernel `.text` + 128 | GRUB `error: bad shim signature.` from its shim-lock verification path | `20261112T025221.507692Z` |

**Control-script markers are instrumentation, not component verdicts.**
The GRUB script prints `NEGATIVE_KERNEL_SIGNATURE_CONTROL` immediately
before `linux /BADKERN.IMG`; the refusal is GRUB's separate
`error: bad shim signature.` message. In that control, firmware and shim
have already accepted their unchanged shim and GRUB predecessors.
Host `sbverify` establishes the signed file's provenance; the paired
positive boot and boundary-specific negative rejection establish
**verification at load time**.

The first two never reach their
`NEGATIVE_CONTROL_UNEXPECTED_REACHED_GRUB` marker. The last reaches GRUB
but does not execute the rejected kernel. Its
`NEGATIVE_KERNEL_LOAD_RETURNED` marker is the next GRUB script command
after the refused `linux`, **not kernel execution**.
Generic `ERROR: Boot option loading failed` lines precede removable-media
fallback when the copied GPT boot entries do not match these small control
disks; they are not evidence that the installed kernel has an invalid
format. In particular, the negative-kernel run must not be mistaken for
the successful installed-image run.

These runs intentionally do not reach Linux or a login prompt; the harness
ends each at its 75-second deadline (exit 1), retaining the rejection UI
and logs. This is expected negative evidence, not three successful OS
boots. `chain-negative-controls.json` in the positive run indexes them.
Together with the unchanged-image positive boot, these controls
demonstrate actual signature enforcement at all three boundaries, rather
than relying only on a true `SecureBoot` variable.


### Repository verification for the Secure-Boot-on follow-on

With no guest running, `python3 -m unittest discover -s tests -q` reports
**413 tests, 51 skipped, OK** (exit 0). The positive run retains the exact
output as `unittest.log`. No QEMU, monitor, firmware or kernel
source was changed for this follow-on, and monitor integration tests were
not rerun; skipped cases are not presented as exercised coverage.
