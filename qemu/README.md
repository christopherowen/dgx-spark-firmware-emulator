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
Only one CPU and GICv3 are supported by this profile. The UART's interrupt
routing and generic PCI topology are approximations; no GPU is implemented.

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
unmodeled `0x180100c0` access and returns nonzero. See [boot status](../docs/boot-status.md).

The patch modifies QEMU GPL-2.0-or-later source and is supplied under that
license, with the original file copyright notices retained. The project's
Apache-2.0 license does not relicense QEMU or the patch. See QEMU's upstream
[LICENSE](https://github.com/qemu/qemu/blob/v11.1.2/LICENSE) and
[virt source](https://github.com/qemu/qemu/blob/v11.1.2/hw/arm/virt.c).
