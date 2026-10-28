# DGX Spark Firmware Emulator

Run DGX Spark SoC boot firmware in QEMU, with original models for the platform
interfaces it needs. The unchanged SoC **2.158.15** UEFI and Standalone MM images
boot to interactive AMI setup and execute an EFI application over native IPv4
PXE. Keyboard navigation, all 13 architectural protocols and persistent NVRAM
are verified. See [measured boot status](docs/boot-status.md) for the evidence.

This is an independent project, unaffiliated with NVIDIA, MediaTek, AMI
or QEMU. Firmware is downloaded from its official source into ignored
local storage and is never redistributed through this repository.

## Requirements

Use macOS or Linux with Python 3.10+, Git, 7-Zip (`7zz` or `7z`), Clang, LLD,
LLVM objcopy and the [QEMU build dependencies](qemu/README.md). Homebrew's
`llvm` and `lld` provide the monitor/probe toolchain on macOS; Ubuntu packages
are `clang lld llvm`.

## Build and boot

Run from the repository root:

```sh
python3 tools/build_qemu.py
python3 tools/boot_spark.py
```

The runner downloads the pinned official LVFS SoC update, verifies its cabinet,
capsule, UEFI and MM hashes, then builds the original lab monitor and boots one
AArch64 CPU under TCG. AMI setup opens over the serial terminal. Arrow keys
navigate; **Ctrl-C** ends the session and saves the emulated variable store.
Use a new emulator process for each cold boot; warm reset is unsupported.

## Verify setup and PXE

```sh
python3 tools/boot_spark.py --smoke-test
python3 tools/boot_spark.py --pxe-test --state-file firmware/state/pxe-nor.bin
```

| Check | Success requires |
| --- | --- |
| Setup | Actual AMI setup, right-arrow navigation to Advanced, all 13 architectural protocols and a saved NOR snapshot |
| IPv4 PXE | Matching DHCP discover/offer/request/ACK, an acknowledged TFTP download matching the exact EFI probe, a fresh execution token and all 13 protocols |

Both checks stop automatically and save NVRAM. Run them again with the same
state file to check a subsequent cold boot. A fresh NOR store uses the vendor
firmware's default IPv4 PXE settings; an existing store must have network boot
enabled. The modes are mutually exclusive. Increase `--timeout` on a slower host;
`python3 tools/boot_spark.py --help` lists all options.

PXE uses Spark's bundled Realtek UNDI/SNP/DHCP/PXE drivers and an original
RTL8168C compatibility NIC. QEMU's restricted user network provides DHCP/TFTP;
only the generated original EFI probe is served. No additional EFI network
driver or iPXE is loaded.

## Local files

| Path | Contents |
| --- | --- |
| `firmware/downloads/`, `firmware/prepared/` | Pinned vendor inputs and unchanged extracted images |
| `firmware/state/` | Persistent emulated NOR; default `spark-nor.bin` |
| `firmware/runs/<UTC timestamp>/` | UART/QEMU logs, memory snapshots, PXE captures, generated probe and `run.json` |
| `firmware/hardware/` | Read-only hardware inventory evidence |
| `build/` | Upstream QEMU sources, dependencies and generated binaries |

These directories are Git-ignored. Select a separate store with
`--state-file firmware/state/another-nor.bin`. Failed runs preserve the previous
store, and concurrent runs cannot share it. Never attach vendor images or
captures to commits, releases, issues or CI artifacts. See
[firmware handling](firmware/README.md).

## Model scope and development

The lab executes real UEFI and MM using a synthetic loader/monitor environment.
Earlier silicon boot stages, GPU and physical NIC firmware, production security
services, secure stage-2 isolation, warm reset and migration are outside the
verified result. IPv6/HTTP boot, Secure Boot image acceptance and OS boot remain
untested. Some board/SMBIOS fields are placeholders.

QEMU is the backend for the SoC/UEFI path. Extending it to isolated controllers
requires identifying their ISA and load contract. A complete Spark model also
needs the platform interfaces exercised by its firmware.

- [Boot status and limitations](docs/boot-status.md)
- [QEMU profile and compatibility NIC](qemu/README.md)
- [Monitor, flash, clock and entropy services](monitor/README.md)
- [Development, diagnostics and hardware collection](docs/development.md)
- [Backend decision](docs/architecture.md), [roadmap](docs/roadmap.md) and [primary sources](research/sources.json)

```sh
python3 -m unittest discover -s tests -v
python3 tools/check_publication.py
```

The Apache-2.0 license covers original tools, monitor and documentation. The QEMU
patch is GPL-2.0-or-later; firmware and external projects retain their licenses.
