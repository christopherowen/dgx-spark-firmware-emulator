# DGX Spark firmware emulation lab

The goal is to execute DGX Spark firmware in a reproducible emulator, including
the MediaTek MT8901-related hardware and firmware interfaces needed along the way.

**Status: real Spark UEFI boots to interactive AMI setup in QEMU.** The unchanged
SoC **2.158.15** UEFI and Standalone MM images run together with this project's
original lab monitor. Main → Advanced keyboard navigation, all 13 required
UEFI architectural protocols, and a second boot using saved NVRAM are verified.
See the [measured boot status](docs/boot-status.md) for scope and limitations.

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

## Boot Spark UEFI

Requires Python 3.10+, 7-Zip (`7zz` or `7z`), Clang, LLD, LLVM objcopy,
and the [QEMU build dependencies](qemu/README.md). On macOS, Homebrew's `llvm`
and `lld` provide the monitor toolchain. On Ubuntu, install `clang lld llvm`.

```sh
python3 tools/build_qemu.py
python3 tools/boot_spark.py
```

The runner downloads pinned official SoC **2.158.15** firmware directly from
LVFS, checks cabinet/capsule/UEFI/MM hashes, and extracts both unchanged images
locally. It builds the original monitor, boots one AArch64 CPU under TCG, and
opens AMI setup over the serial terminal. Arrow keys navigate; **Ctrl-C** ends
the session and saves the emulated variable store. Exit setup's menus without
requesting a firmware reboot; use a new emulator process for a cold boot.

For an automated check that opens setup, navigates to Advanced, verifies all
architectural protocols, saves NVRAM, and stops:

```sh
python3 tools/boot_spark.py --smoke-test
```

Run it twice to exercise persistence. NVRAM is in `firmware/state/spark-nor.bin`;
per-run UART logs, a DXE snapshot, input hashes and `run.json` are in
`firmware/runs/`. All firmware, state, captures and generated binaries are
Git-ignored. Select a separate fresh store with
`--state-file firmware/state/another-nor.bin`.

This boots the UEFI payload and its real MM variable firmware using an emulated
loader/monitor environment. Earlier silicon boot stages, GPU/NIC devices,
production security services and an OS are outside the verified result. RoT
requests receive explicit errors. Some cached board/SMBIOS fields are placeholders,
including setup's total-memory display. See the [monitor](monitor/README.md)
and [profile](qemu/README.md) for modeled interfaces.

The earlier diagnostic command remains available:

```sh
python3 tools/run_spark.py
```

It checks exact SEC/DXE entry and first-instruction single-steps on stock QEMU.
Its separate continuation experiments omit MM and are expected to fail; use
`boot_spark.py` for the complete setup path.

## Collect read-only hardware evidence

```sh
python3 tools/collect_hardware.py USER@HOST
```

Requires SSH access and passwordless `sudo` for inventory reads. Captures include
ACPI tables, resource/interrupt maps, board and firmware versions, PCI inventory,
and kernel logs. They stay locally in ignored `firmware/hardware/`. The tool
does not access device registers directly, flash firmware, or change the host.
See [hardware findings](docs/hardware-findings.md) for reviewed address facts.

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
GICv3 identification, UART interrupt delivery, EINT software interrupts, and
Root of Trust error replies with original synthetic code. Vendor
firmware is never downloaded by CI or uploaded as an artifact.
The Apache-2.0 license covers this project's own tools and documentation. The QEMU
patch is GPL-2.0-or-later; firmware and external projects retain their licenses.

The first Spark-specific execution checkpoint is implemented. Next is modeling
the remaining platform and secure firmware services toward the firmware console.
