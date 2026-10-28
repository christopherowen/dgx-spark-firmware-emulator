# DGX Spark firmware emulation lab

The goal is to execute DGX Spark firmware in a reproducible emulator, including
the MediaTek MT8901-related hardware and firmware interfaces needed along the way.

**Status: initial research and tooling. No DGX Spark firmware boots here yet.**
There is no GB10/MT8901 machine model in this repository. The working QEMU test
boots generic EDK II firmware on QEMU's `virt` board and reaches its UEFI shell.
It establishes a host tooling baseline, not DGX Spark compatibility.

## Approach

Start with **QEMU for the application processor / SoC boot and UEFI path**.
Evaluate **Renode for isolated controllers and peripheral models** after the
actual firmware, instruction set, reset state, and interfaces are identified.
Renode has Arm64 support; it should not be dismissed as MCU-only. Neither
backend gives us a Spark model simply by selecting an Arm CPU.

MediaTek MT8901 appears in NVIDIA's Linux pinctrl, peripheral, and power-control
work. Those drivers are evidence about interfaces; they do not establish the
ISA or boot flow of every internal firmware processor. This project keeps
unknown addresses and entry points explicitly unknown.

See the [backend decision](docs/architecture.md), [milestones](docs/roadmap.md),
and [primary sources](research/sources.json). Findings were checked on
2026-10-28. This is an independent project, unaffiliated with NVIDIA, MediaTek,
Antmicro, or QEMU.

## Download firmware locally

Firmware may not be redistributed through this project. Download it directly
from its vendor/LVFS source into **`firmware/downloads/`**, which is Git-ignored.
The downloader always resolves that directory relative to this checkout,
regardless of your current working directory.

Supply the direct HTTPS download URL and its SHA-256 from upstream metadata:

```sh
python3 tools/download_firmware.py "$FIRMWARE_URL" \
  --sha256 "$FIRMWARE_SHA256" --filename spark-soc.cab
```

The download is streamed to a temporary file, verified, then published locally.
Its source URL, checksum, and size are saved alongside it in the same ignored
directory. Mismatched downloads are discarded; existing files are preserved.
The downloader refuses destinations that are tracked, unignored, or reached
through symbolic links. It does not extract or install the image.

Do not attach firmware to commits, GitHub releases, issues, or CI artifacts.
Only downloader code and references to upstream sources belong in this repo.

## Inspect a local firmware artifact

Requires Python 3.10 or newer; no Python packages are needed.

```sh
python3 tools/spark_fw.py inspect /path/to/firmware.cap
mkdir -p build
python3 tools/spark_fw.py inspect /path/to/component.elf > build/inventory.json
```

The inspector reads a file, computes its SHA-256, and reports recognized
top-level ELF, PE/COFF, FMP capsule, or CAB header metadata as JSON. Other
formats remain `unknown`. It does not unpack, authenticate, execute, or flash
firmware. A recognized header does not establish a runnable firmware image,
and a raw binary does not establish an instruction set.

Keep images in the ignored `firmware/` directory or outside the checkout.
Review generated metadata before publishing it. See [artifact handling](firmware/README.md).

## Run the generic QEMU baseline

Install `qemu-system-aarch64` and generic AArch64 EDK II firmware containing an
internal UEFI shell. On Apple Silicon with Homebrew QEMU:

```sh
python3 tools/spark_fw.py qemu-smoke \
  --firmware /opt/homebrew/share/qemu/edk2-aarch64-code.fd
```

On Ubuntu with `qemu-system-arm` and `qemu-efi-aarch64` installed:

```sh
python3 tools/spark_fw.py qemu-smoke \
  --firmware /usr/share/qemu-efi-aarch64/QEMU_EFI.fd
```

The test uses software emulation, one Cortex-A72, a generic `virt` machine,
512 MiB RAM, no network, and no attached host disks. **Cortex-A72 is only a
baseline CPU choice; it does not model the GB10 CPU.** The firmware file is
read-only. After 20 seconds the process is stopped, and success requires the
`UEFI Interactive Shell` marker with no earlier QEMU process failure. Logs and
run metadata go into ignored `build/`. Use `--timeout 60` on a slower host.

This command accepts generic QEMU firmware, not an NVIDIA update capsule.
Missing TPM or entropy-device diagnostics can appear on this minimal machine;
the pass condition is shell entry, not a clean platform initialization report.

## Development

```sh
python3 -m unittest discover -s tests -v
python3 tools/check_publication.py
```

CI rejects tracked firmware/local artifacts, runs synthetic inspection/download
tests, and runs the generic QEMU/EDK II smoke test. Vendor firmware is never
downloaded by CI or uploaded as an artifact. All test inputs are synthetic.
The Apache-2.0 license covers this project's own code and documentation. Firmware
and external projects retain their respective licenses.

The first Spark-specific milestone is to identify one firmware component,
establish its load/entry contract, and execute it to a repeatable checkpoint
with recorded peripheral accesses. Full system boot is a later milestone.
