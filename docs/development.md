# Development and diagnostic tools

Run commands from the repository root. The [main README](../README.md) covers
normal UEFI and PXE use; [boot status](boot-status.md) defines the verified result.

## Things the harness refuses to do quietly

- It will not describe RAM differently to two consumers: the loader tag and the monitor's FF-A bound
  take the same top, so a larger `--memory` cannot advertise memory the FF-A path then refuses.
- It will not claim a verification from an earlier reboot cycle: any stop requires the setup screen,
  the keyboard navigation or the PXE marker to be the last one in the capture and to belong to the
  cycle the snapshot is taken from. The decision is made from the capture itself after the UART is
  drained, not from flags, because the same marker appears in every cycle.
- It will not name firmware it did not load: `uefi_sha256` is the hash of the image that was booted,
  `uefi_pinned_sha256` is the release it was asked for, and `firmware_modified` says when they differ.
- It will not trade the flash for a reset: the loader's window is a seed, and the live window is the
  one the harness dumps.

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
python3 tools/probe_mm.py
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
| `tools/probe_mm.py` | Local unchanged-STMM buffer-discovery and pointer-rejection probe |
| `tools/qemu_control.py` | Shared QMP/GDB clients and connection ownership |
| `tools/handoff.py` | Full-boot and diagnostic loader memory tags |
| `tools/spark_image.py`, `tools/prepare_mm.py` | Pinned local UEFI/MM extraction and hash checks, per release |
| `tools/nor_image.py`, `tools/nor_edit.py`, `tools/store_diff.py` | Validate, list/edit and compare an NVAR store |
| `tools/module_map.py` | FFS volume walk and code location inside a firmware image |
| `tools/hang_probe.py` | Sample a boot that never reaches setup and localise where it stops |
| `tools/pxe.py`, `uefi/pxe_probe.c` | Original EFI probe build and packet evidence checks |
| `monitor/` | Original EL3/S-EL2 monitor, bounded FF-A sharing and functional flash service |
| `qemu/` | Pinned upstream patch and device-model documentation |
| `tests/` | Synthetic fixtures and unit/integration checks |
| `research/sources.json` | Reviewed primary-source references |

Upstream source, host tools and generated monitor binaries belong in ignored
`build/`. Vendor images, persistent NOR, captures and run evidence belong in
ignored `firmware/`. Keep the original acquisition evidence when reorganizing
local experiments; it is not disposable build output.

## Flash policy and size

The board's flash service is gated: command 8 arms a latch for a trusted sender, and once
armed a non-trusted request is refused with status `0x0A`. The monitor models that gate, and
`tools/build_monitor.py --flash-policy open` starts it in the pre-arming window when a probe
needs to reach the service before the latch closes. The default is `armed`, which is what
the board is by the time a payload executes.

Flash size is one value end to end and it is taken from the state file you pass, so an
existing 1 MiB or 32 MiB fixture keeps working; `--nor-size` overrides it and must be
`0x100000` (the legacy model), `0x2000000` or `0x4000000`. With no state file yet, the model is 32 MiB. The board's part is
64 MiB:

```sh
python3 tools/boot_spark.py --smoke-test --nor-size 0x4000000 --state-file firmware/state/board.bin
python3 tools/build_monitor.py --nor-size 0x4000000          # same value for the monitor
```

A program or erase on a real part takes milliseconds; `--write-us` / `--erase-us` model that
latency. A program commits `NOR_PAGE` (256 bytes) at a time and spends that page's time, so a reset
inside the window keeps the pages already committed; at the default 0 the bytes are identical to
committing the request in one go. `--board` supplies datasheet-typical values (400 µs per page,
40 ms per sector erase) when the caller does not.

## Seeing what the firmware asks for

`--flash-trace` builds a monitor that prints one line per answered flash request — command, payload
length, address, count and status. It changes no decision, only what is printed, and it is the
quickest way to check that a probe's traffic matches the shapes the model expects.

```sh
python3 tools/boot_spark.py --smoke-test --board --flash-trace --firmware-version 2.158.15 \
    --flash-image firmware/state/board.bin --timeout 80
grep 'FLASH cmd' firmware/runs/<id>/serial.log
```

The trace build also prints every SMC that reaches the monitor's exception handler (`fid`, caller
state, first two arguments and the caller's address), which is how the firmware's SMCI/PSCI usage
was enumerated.

With the trace build the run's `run.json` carries `flash_requests` and `smc_calls`, the counts of
answered flash requests and of SMCs that reached the monitor, so a measurement can be quoted
without re-reading the log.

`--accept-mmap-notify` answers the firmware's DRAM notification (`0xc4000122`) with success instead of
`NOT_SUPPORTED`. It exists so the two answers can be compared boot for boot; with the default monitor a
boot lands on the same store image either way, so the choice does not affect measured behaviour.

## Counting reboot cycles instead of seconds

`--stop-after-boots N` stops a run after N reboot cycles and records `boots_observed`. Use it for
class comparisons: a stalled store resets and restarts, so a wall-clock deadline measures the host's
speed as much as the firmware's behaviour. A boot-count stop on a store that never reaches setup
reports that plainly rather than passing the snapshot checks.

## What a run record identifies

`run.json` carries the conditions a result depends on, so it can be reproduced from itself: the
layout (`nor_offset`, `code_offset`, `nor_size`), the window (`timeout`), the model's latencies and
policy, the guest clock and seed, the diagnostic switches (`flash_trace`, `accept_mmap_notify`,
`gwdt_timeout_ms`), `memory` with `memory_usable`, and the identity of all four artefacts —
`uefi_sha256`, `mm_sha256`, `monitor_sha256` and `qemu_sha256`.

## Memory

`--memory` is what QEMU allocates and it must be at least `2G` — the loader tag's fixed regions end at
`0xC0000000`, so a smaller allocation could not be described. What the guest can use is less than
the allocation. RAM is mapped from `0x40000000`, the
profile aliases PCI BAR space over the lowest gigabyte, and the loader tag in `tools/handoff.py`
describes the remainder up to the end of the allocation — so `-m 2G` gives the firmware
`0x80000000..0xC0000000` (one gigabyte) and `-m 3G` gives `0x80000000..0x100000000` (two). `run.json`
records both `memory` and `memory_usable`, and the handoff is byte-identical to the original fixed tag
at the 2 GiB default.

## The whole device, not just the store

The board's part holds two 32 MiB volumes: the variable store at `0x0` and the code volume at
`0x2000000`. `--flash-image` boots from one image of that shape, slicing the boot image out of the
code volume, so a guest write and the next boot read the same bytes. `--board` sets the whole
set: 64 MiB, code offset `0x2000000`, and the datasheet part times.

```sh
python3 tools/board_image.py compose --store firmware/state/store.bin \
    --code firmware/prepared/2.158.15/uefi.fd --out firmware/state/board.bin
python3 tools/boot_spark.py --smoke-test --board --firmware-version 2.158.15 \
    --flash-image firmware/state/board.bin --timeout 80
```

**A run writes back to the image it was given** — that is the point, and it is also a trap: a
second boot of the same file starts from the first boot's result. For a paired experiment,
compose the image again (and give `compose` a path that does not exist yet — it refuses to
overwrite).

`tools/board_image.py split` recovers the two halves from a device image, and `inspect` reports
where each volume starts and whether its `_FVH` signature is where it should be.

The [MM sharing notes](mm-sharing.md) distinguish FF-A transaction registration
from the firmware's fixed MM communication buffer and describe the local probe's
execution-breakpoint evidence and error-delivery limitation.

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

## Two CPUs, and what the second one does

The profile models one or two CPUs. With `--smp 2` the machine starts the primary and holds the
second parked: the monitor implements no PSCI, so nothing services `CPU_ON`. That is measured, not
assumed — a run tracing the firmware entry with `--smp 2` records `trace_per_cpu: {"cpu0": 1}` and
`secondary_cpus: "parked until started"`, so a receipt cannot imply parallel execution that did not
happen.

## Asking the firmware what settings exist

`tests/fixtures/nvram_census.c` is an EFI application (PXE lane) that enumerates every UEFI
variable — name, GUID, attributes, size, and the bytes for values up to 512 bytes — and walks the
ACPI tables through the configuration table, printing each table's signature, length and address.
Under the profile it returns the firmware's own answer: on 2.158.15, 69-74 variables including
`Setup`, `SecureBoot`, `AMITCGPPIVAR` and `NvmeDynamicSetupVar`, and 17 tables including FACP,
APIC, GTDT, PPTT and four SSDTs. `tests/test_census.py` asserts that shape rather than a snapshot,
because the variable set follows the store the boot used.

Two things it settles for configuration work: which settings are NVRAM-backed (a `Setup` blob of
0x51 bytes is present and readable), and which interface an ACPI table would describe. Decoding the
`Setup` fields needs the HII forms from the firmware image, and the SSDTs at the printed addresses
are where an EC or fan interface would live.

## Running a whole Linux image, not just firmware

`tools/run_linux.py` boots a kernel and initramfs under this QEMU build and records the run the same
way a firmware boot is recorded: machine, arguments, kernel and initramfs hashes, the QEMU binary's
hash, the console log, and a verdict — `kernel`, `userspace`, or `not-booted`.

With the vendor recovery archive it does the extraction itself (cached under
`firmware/downloads/recovery-extract/`, never in the tree):

```sh
python3 tools/run_linux.py --recovery firmware/downloads/dgx-spark-recovery-1.155.49.tar.gz --timeout 90
```

That boots `7.0.0-1018-nvidia` and its installer initramfs to userspace: the console shows the
installer's own banner (`--- FastOS Version`, `NAME="DGX Spark FASTOS"`, `VERSION="1.155.49"`) before
it starts looking for its media. `tests/test_linux_boot.py` asserts exactly that, so the milestone is
a regression test rather than a lucky run.

What it does not do yet: run under the firmware profile (the handover path), find the installation
media, or write to a target disk. Those are the next steps, and each one is a modelling question the
harness will make visible rather than a guess.

## What the emulator can prove about the levers, in one place

| lever | proof in this tree | limit |
| --- | --- | --- |
| fans, contract | `monitor/fan.c` answers `0xC400008D` exactly as `\_SB.FAN0.WFCF` builds it; `tests/test_fan_emulator.py` drives it through a firmware-delivered probe and both fans read back (`R0 4350`, `R1 4350`) | the EC is not modelled; the model is the contract |
| fans, exact AML | `tests/test_fan_aml.py` executes the vendor's `_FSL`/`_FST` under ACPICA; `_FST` reports the level just set | the SMC itself is inert under ACPICA (no FFH handler) |
| fans, through Linux | the profile boots the vendor kernel to userspace (`tests/test_profile_userspace.py`); on a unit with `CONFIG_ACPI_FFH=y` the vendor kernel services the `FFixedHW` region and `\_SB.FAN0._FST` returns live per-fan rpm | the *recovery* kernel inside QEMU has no FFH handler, which is what the earlier "REFUSED" recorded — that is a property of that guest, not of the firmware |
| fans, newest firmware | `FAN-OK` on LVFS `0x2009e0f` and on the Dell 4.1.0 capsule (`research/firmware-catalog.json`) | the EC firmware (`0x3000d07`) is not emulated |
| power limits | the monitor serves the OS/effective PL slots; the probe writes PL1=45000, reads the effective value, restores (`PL-OK`) | a live unit needs the base address, now derived (0x05170000) |
| CPU frequency | `tools/cpu_clocks.py` drives the kernel's cpufreq with a saved backup and a refusal for ceilings the firmware does not offer | the profile has no cpufreq policy: the CPPC registers are zero in the model, so `cppc_cpufreq` does not bind |
| CPU placement (measured on a unit) | two clusters: **ten cores at 3.9 GHz** and **ten at 2.808 GHz**, `performance` governor, `boost` disabled; pinning with `taskset -c 5-9,15-19` is the supported lever and needs no firmware | single-threaded gain is a placement effect, not a clock increase; the DSDT cannot state the ceiling because `_CPC` carries register descriptors, not values |
| thermal zones | the seeded SOC_THERM block reads back through the vendor's `\_TZ.RREG`: 44.9 / 39.9 / 49.9 / 34.9 °C in the guest | a seed, not a sensor model |
| DRAM timings | `tools/timing_tables.py` finds and extracts `BCT`/`DDRFSK`/`MISC`; `research/dram-knobs.json` records the firmware's own knob vocabulary | `BCT` carries a four-switch patch (`research/bct-patch.json`); `DDRFSK` and `MISC` are blank in every artifact we hold |

## What can be changed, and what cannot

Three levers were investigated for tuning a Spark from software. Each has a tool, a test and a limit
written down where it belongs:

| lever | what exists here | limit |
| --- | --- | --- |
| **fans** | `tools/fanctl.py` drives the firmware's own ACPI fan device through `/sys/class/thermal` (`_FSL` 0..100), reading `max_state` from the device | the EC's actual response needs a unit; the model in `monitor/fan.c` is the interface, not the fan |
| **power limits / clocks** | `tools/pl_limits.py` reads and writes the OS PL1..PL4 slots the firmware's interface table names, with backup and restore; the semantics are demonstrated in QEMU (`PL-OK`); the base is derived from the DSDT (`\_SB.MTEL` `Memory32Fixed (0x05170000, 0x60000)`, `_CPC` registers at `+0x5C404`) | SPBM, which arbitrates the claims, ships encrypted; the eSPI service's own PL registers are readable only if its gateway accepts an id the firmware does not document (`research/espi-spbm-registers.json`) |
| **CPU placement** | measured on units: ten cores at 3.9 GHz, ten at 2.808, `performance` governor, capacity-aware scheduling already preferring the fast ten (`research/cpu-clusters.json`) | `taskset` guarantees placement and removes variance; it does not uncover an idle faster half, and `boost` (present, `0`) has an unmeasured effect |
| **DRAM timings** | `tools/timing_tables.py` finds and extracts the `BCT`/`DDRFSK`/`MISC` partitions whenever an artifact carries them | `DDRFSK` and `MISC` are blank end to end in the capsules and in the recovery image; `BCT` is a patch, not a table (`docs/ram-timings.md`) |
| **the capsule's own contents** | `tools/container.py` (declared base + self-described extents), `tools/spkg.py` (SPKG headers and FF-A manifests), `tools/anchors.py` (string references by file offset, base-independent) | the 15 encrypted components are AEAD with unique nonces; a family key in ROM or fuses is not in any artifact |
| **what a running unit exposes** | `tools/fleet_probe.py` reports, read-only, whether the firmware carries the fan service and whether anything exposes a fan device | it observes; it does not test whether the service answers a read |

And one thing that is now possible: `tools/run_linux.py` boots a whole Linux image — the vendor's
recovery kernel and installer initramfs reach userspace under QEMU — which is the platform any future
measurement of these levers on a real image would start from. Booting that same kernel under the
firmware profile works too, and slowly: `docs/linux-under-the-profile.md` records the four things it
took (the SCR enables, the console on the command line, LPIs off, and the block the firmware
declares). Under TCG the guest spends about two minutes of wall time on each second of kernel time,
so `--timeout` accepts up to an hour for a boot that is expected to reach userspace.
