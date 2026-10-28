# Experimental QEMU address profile

The patch applies to QEMU **v11.1.2** and adds the opt-in machine property
`-M virt,spark-fw-lab=on,virtualization=on,gic-version=3`. The ordinary `virt`
configuration keeps its existing behavior.

This is a firmware bring-up profile, not a complete GB10 or MT8901 model. It
uses existing QEMU device implementations at addresses observed in the pinned
Spark firmware: GIC distributor `0x06800000`, one redistributor at `0x06880000`,
and a 16550 UART at `0x16a00000` with register shift 2. It moves/reduces the
generic flash and PCI MMIO windows to avoid overlapping those devices.
It also provides eleven GPIO/pad configuration banks with register readback
and value/SET/CLEAR aliases, including the early UART mux access. External
pins, GPIO interrupts, and electrical/clock effects are not modeled.
The UART drives GSI 878, confirmed against physical SPCR and tested in QEMU.
The GIC exposes interrupts through ID 895 to include this line. Four MT8901
EINT banks implement configuration readback, SET/CLEAR masks, software pending
and acknowledgement, and an aggregate parent interrupt at GSI 563. External
pin signals and debounce timing are not modeled. See the reviewed
[hardware findings](../docs/hardware-findings.md) and public register references.

Only one CPU and GICv3 are supported by this profile; physical ACPI reports
GICv4. PCIe roots have no downstream devices, and no GPU is implemented. Use TCG
and a fresh QEMU process per run; reset, migration, and snapshot restoration
of the added peripheral state are not supported.

Build prerequisites are a C compiler, Python with venv/pip support, Git, tar,
pkg-config, and the GLib and pixman development libraries. The build tool
installs Ninja into an ignored local virtual environment. QEMU's configure
step may fetch its own Python/subproject dependencies.

On macOS with Homebrew, install `glib`, `pixman`, and `pkgconf`. On Ubuntu,
the packages are `build-essential python3-venv git pkg-config libglib2.0-dev
libpixman-1-dev`. Then run from the repository root:

```sh
python3 tools/build_qemu.py
python3 tools/boot_spark.py --smoke-test
python3 tools/boot_spark.py
```

QEMU source, dependencies, and binaries stay under ignored `build/`. Vendor
firmware, generated handoff data, and execution traces stay under ignored
`firmware/`. No vendor payload is included in this patch.

The full boot runner also requires Clang, LLD and LLVM objcopy (`clang lld llvm`
on Ubuntu; `llvm` and `lld` with Homebrew). It sets `secure=on`, loads this
project's original monitor and the unchanged vendor Standalone MM package,
and boots real Spark UEFI to interactive AMI setup. See [boot status](../docs/boot-status.md)
for measured results and [monitor details](../monitor/README.md).

Additional bring-up interfaces are:

- A shared 256 KiB loader-cache SRAM window at `0x00100000`, initially zero.
- Bounded USB PHY/control configuration banks, with register readback. Six
  existing QEMU xHCI cores occupy the firmware's host apertures. Corresponding
  IPPC status reflects reset/power state and exposes one USB2/USB3 port per
  virtual controller. PHY timing and electrical effects are not modeled;
  xHCI interrupt lines are not routed, and UEFI uses polling.
- PCIe PHY/system configuration readback and 15 minimal type-1 root-port
  configuration spaces at the observed ECAM bases. Downstream accesses read
  all ones, BARs are absent and links remain down. This is an empty topology,
  not a model of the physical GPU, NVMe or network cards.
- Synthetic board and fuse configuration, including a one-CPU enable mask.
  Most fields and loader-cached board information remain zero placeholders.

The enlarged GIC also adjusts the CPU timer/PPI input base to match its SPI
count. The lab monitor wakes the redistributor before entering UEFI. A
synthetic test verifies timer PPI 30 as well as monitor context switches.

`spark-rot-error-replies=on` models a polled FIFO transport at `0x18010000`
and `0x18020000`. Every request receives a nonzero service status. It implements
no security or logging service and executes no Root of Trust firmware. The
transport is inferred from the UEFI caller, not verified by live register
transactions. The full boot runner explicitly enables this failure behavior.
The older diagnostic runner can still select it with `--rot-error-replies`.

The added configuration-bank state is for cold boot only; warm reset,
migration and restoring QEMU snapshots are unsupported. Persistent emulated
NOR is saved separately by the full boot runner.

The patch modifies QEMU GPL-2.0-or-later source and is supplied under that
license, with the original file copyright notices retained. The project's
Apache-2.0 license does not relicense QEMU or the patch. See QEMU's upstream
[LICENSE](https://github.com/qemu/qemu/blob/v11.1.2/LICENSE) and
[virt source](https://github.com/qemu/qemu/blob/v11.1.2/hw/arm/virt.c).
