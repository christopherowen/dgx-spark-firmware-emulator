# Development and diagnostic tools

Run commands from the repository root. The [main README](../README.md) covers
normal UEFI and PXE use; [boot status](boot-status.md) defines the verified result.

## Checks

The standard-library tests use original synthetic fixtures. Without a custom
QEMU build, emulator integration tests are skipped:

```sh
python3 -m unittest discover -s tests -v
```

After building the [QEMU profile](../qemu/README.md), include its peripheral,
NIC, monitor and TRNG integration tests:

```sh
SPARK_QEMU=build/qemu-spark/qemu-system-aarch64 python3 -m unittest discover -s tests -v
```

For local tests with the pinned vendor firmware:

```sh
python3 tools/boot_spark.py --smoke-test
python3 tools/boot_spark.py --smoke-test
python3 tools/boot_spark.py --pxe-test --state-file firmware/state/pxe-nor.bin
```

Stage intended source changes, then check the index before committing:

```sh
python3 tools/check_publication.py
```

CI checks publication, synthetic tests, a generic UEFI baseline, and the custom
QEMU build and device tests. It never downloads Spark firmware or uploads run
artifacts. Firmware-dependent boot checks are local only.

## Source layout

| Location | Purpose |
| --- | --- |
| `tools/boot_spark.py` | Interactive setup, setup verification and PXE verification |
| `tools/qemu_control.py` | Shared QMP/GDB clients and connection ownership |
| `tools/handoff.py` | Full-boot and diagnostic loader memory tags |
| `tools/spark_image.py`, `tools/prepare_mm.py` | Pinned local UEFI/MM extraction and hash checks |
| `tools/pxe.py`, `uefi/pxe_probe.c` | Original EFI probe build and packet evidence checks |
| `monitor/` | Original EL3/S-EL2 monitor and functional flash service |
| `qemu/` | Pinned upstream patch and device-model documentation |
| `tests/` | Synthetic fixtures and unit/integration checks |
| `research/sources.json` | Reviewed primary-source references |

Upstream source, host tools and generated monitor binaries belong in ignored
`build/`. Vendor images, persistent NOR, captures and run evidence belong in
ignored `firmware/`. Keep the original acquisition evidence when reorganizing
local experiments; it is not disposable build output.

## SEC/DXE entry diagnostic

```sh
python3 tools/run_spark.py
```

This older diagnostic verifies exact SEC/DXE entry and first-instruction
single-steps using stock QEMU. It deliberately omits the monitor and real MM.
Its optional continuations are failure investigations, not full-boot checks.
Use `python3 tools/run_spark.py --help` for those options and
`tools/boot_spark.py` for the supported full-boot path.

## Collect read-only hardware evidence

```sh
python3 tools/collect_hardware.py USER@HOST
```

Requires SSH access and passwordless `sudo` for inventory reads. Captures include
ACPI tables, resource/interrupt maps, board and firmware versions, PCI inventory,
and kernel logs. They stay locally in ignored `firmware/hardware/`. The tool
does not access device registers directly, flash firmware, or change the host.
See [hardware findings](hardware-findings.md) for reviewed address facts.

## Download other firmware locally

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
Review generated metadata before publishing it. See [artifact handling](../firmware/README.md).

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
