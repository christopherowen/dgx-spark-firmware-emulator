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
GICv4. Generic PCI topology is approximate, and no GPU is implemented. Use TCG
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
python3 tools/run_spark.py --qemu build/qemu-spark/qemu-system-aarch64 \
  --experimental-address-profile --continue-seconds 5
```

QEMU source, dependencies, and binaries stay under ignored `build/`. Vendor
firmware, generated handoff data, and execution traces stay under ignored
`firmware/`. No vendor payload is included in this patch.

The current continuation reaches serial checkpoint `61`, then faults at an
unmodeled `0x180100c0` access and returns nonzero.

The separate `spark-rot-error-replies=on` property, exposed by the runner's
`--rot-error-replies` option, models a polled FIFO transport at `0x18010000`
and `0x18020000`. Every request receives a nonzero service status. It implements
no security or logging service and executes no Root of Trust firmware. The
transport is inferred from the UEFI caller, not verified by live register
transactions. This failure scenario reaches checkpoints `D3` and `90`, then
reports missing architectural protocols and faults. See
[boot status](../docs/boot-status.md).

The patch modifies QEMU GPL-2.0-or-later source and is supplied under that
license, with the original file copyright notices retained. The project's
Apache-2.0 license does not relicense QEMU or the patch. See QEMU's upstream
[LICENSE](https://github.com/qemu/qemu/blob/v11.1.2/LICENSE) and
[virt source](https://github.com/qemu/qemu/blob/v11.1.2/hw/arm/virt.c).
