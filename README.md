# DGX Spark firmware emulation lab

The goal is to execute DGX Spark firmware in a reproducible emulator, including
the MediaTek MT8901-related hardware and firmware interfaces needed along the way.

**Status: real Spark UEFI executes through SEC and DXE entry.** The experimental
QEMU profile also reaches serial **`Checkpoint 61`**. Full boot is incomplete:
the current run faults in `SaveApplLog` at the unmodeled `0x180100c0` access.
There is no complete GB10/MT8901, secure-monitor, or GPU model. See the
[measured boot status](docs/boot-status.md).

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

## Execute real Spark firmware

Requires Python 3.10+, QEMU with AArch64 TCG, and `7zz` or `7z`:

```sh
python3 tools/run_spark.py
```

This downloads pinned official SoC **2.158.15** firmware directly from LVFS,
verifies cabinet/capsule hashes, and extracts the checksum-verified 32 MiB UEFI
image locally. Images and logs stay under ignored `firmware/`.

The runner supplies a synthetic loader memory handoff, verifies exact SEC and
relocated DXE entry breakpoints, and single-steps each first instruction. It
does not modify the vendor image or run the preceding boot ROM. Success means
**SEC/DXE entry**, not a full boot or UEFI shell.

For further platform initialization, build the optional [QEMU profile](qemu/README.md):

```sh
python3 tools/build_qemu.py
python3 tools/run_spark.py --qemu build/qemu-spark/qemu-system-aarch64 \
  --experimental-address-profile --continue-seconds 5
```

The continuation reaches checkpoint `61`, then currently returns nonzero for
the firmware exception. Per-run UART output, traces, registers, and `run.json`
remain local under `firmware/runs/`.

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

CI rejects tracked firmware/local artifacts, runs synthetic tests and the generic
QEMU/EDK II smoke test, and builds the custom QEMU profile to test GPIO aliases,
GICv3 identification, and serial output with original synthetic code. Vendor
firmware is never downloaded by CI or uploaded as an artifact.
The Apache-2.0 license covers this project's own tools and documentation. The QEMU
patch is GPL-2.0-or-later; firmware and external projects retain their licenses.

The first Spark-specific execution checkpoint is implemented. Next is modeling
the remaining platform interfaces to continue beyond checkpoint `61` toward
the firmware console.
