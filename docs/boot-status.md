# Measured Spark boot status

Verified locally on 2026-10-28 with QEMU 11.1.2 TCG, one AArch64 `max` CPU,
and a synthetic loader handoff. **Full Spark boot is incomplete.**

## Firmware inputs

The source is official LVFS component `com.nvidia.dgx.spark.socfw.firmware`,
release **152274**, version `0x02009e0f` / **2.158.15**. The direct upstream URL
and cabinet, capsule, and decoded UEFI SHA-256 values are pinned in
`tools/spark_image.py`. Only references and hashes are published.

The container chain is CAB → FMP v3 capsule → authentication wrapper → MSS1
header → MediaTek `SF_BOOT` image → FILEINFO LZMA stream → 32 MiB UEFI volume.
Extraction checks bounds and exact hashes. It does not independently validate
the PKCS#7 trust chain or emulate secure boot.

The reset branch and first PE image agree on firmware base `0x80800000` and
SEC entry `0x80802f94`. The synthetic handoff supplies MMIO, `MMAP_TINY_UEFI`,
and `MMAP_FREE_BASE_1` entries in memory tag `0x88610005` at `0x80010000`.
Its full `0x1868`-byte size is required because SEC copies that complete
structure into a GUID HOB. This is an inferred test-entry contract, not a
physical-device capture or a reproduction of the preceding silicon boot ROM.

## Verified results

| Checkpoint | Result |
| --- | --- |
| SEC entry `0x80802f94` | Exact execution breakpoint and first-instruction single-step verified |
| MMU setup and HOB construction | Executed with synthetic loader input |
| Relocated DXE core entry `0x847c96fc` | Exact execution breakpoint and first-instruction single-step verified |
| GPIO aliases, GICv3 ID, and 16550 serial | Tested with original synthetic AArch64 probe |
| UART GSI 878 and EINT aggregate GSI 563 | Synthetic guest interrupt tests pass |
| Vendor serial output with custom profile | `Checkpoint 61` |
| Opt-in Root of Trust error replies | `Checkpoint 61`, `D3`, and `90`, then exception |
| Firmware console / setup menu / OS boot | Not achieved |

The DXE allocation is deterministic for this image and handoff. The runner
fails if the exact checkpoint is not reached; it records the PC before and
after each single-step. A translated instruction trace alone does not count
as execution evidence. The default command stops after the two entry checks.

## Current blocker

Without error replies, after checkpoint `61`, `SaveApplLog` loads at
`0x8676f000`. It faults at PC **`0x86770350`**, writing **`0x180100c0`**.
The serial console reports a synchronous exception and the exception handler
enters a dead loop. Physical ACPI identifies this region as `ROT0`, HID
`NVDA8600`, a Root of Trust interface. Treating it as generic PCI configuration
does not solve the protocol: the firmware polls indefinitely. That experiment
is not included in the profile.

With `--rot-error-replies`, a bounded polled FIFO at ROT0/ROT1 echoes the
message ID with zero payload words and nonzero service status 1. ACK and RX
pending/W1C behavior follows the observed UEFI caller. This is an explicit
failure scenario: it never returns service success, accesses no host device,
and executes no Root of Trust firmware. The status number has no claimed
vendor-specific meaning. The interface has not been validated by physical
register transactions.

That scenario reaches `D3`, prints `ERROR: Some Architectural Protocols are
not available`, reaches `90`, and finally reports a synchronous exception at
`0x0`. Inspection of the paused DXE core's complete architectural-protocol
table at `0x847dc928` confirms:

| Present | Missing |
| --- | --- |
| Security, Cpu, Metronome, Timer, WatchdogTimer, Runtime, Reset | Bds, Variable, VariableWrite, Capsule, MonotonicCounter, RealTimeClock |

The runner validates all 13 ordered public protocol GUID pointers and the
null table terminator before reporting these flags. If the layout changes,
it records an inspection error instead of guessing. The snapshot remains
local under `firmware/runs/`.

The trace also records SMC requests, including `0xc4000066`, the FF-A RX/TX
mapping function defined by [EDK II's FF-A ABI header](https://github.com/tianocore/edk2/blob/master/MdePkg/Include/IndustryStandard/ArmFfaSvc.h).
The current profile supplies QEMU's PSCI conduit, which returns unsupported
for this function; it supplies no secure partition manager or MM service.
The missing protocols identify further work, not proof that one stubbed call
would complete boot.

The profile uses QEMU's existing GICv3 and 16550 models at observed firmware
addresses, plus GPIO/pad configuration banks with value/SET/CLEAR readback.
Four EINT banks provide configuration, software pending/acknowledgement, and
parent interrupt aggregation. External GPIO/EINT input, electrical effects,
and debounce/clock timing are unimplemented. The generic PCI topology remains
approximate. No GB10 GPU or secure partition execution is provided. The
[hardware inventory](hardware-findings.md) confirms addresses and interrupt
assignments but runs a different SoC firmware revision.

## Reproduce

```sh
python3 tools/run_spark.py
python3 tools/build_qemu.py
python3 tools/run_spark.py --qemu build/qemu-spark/qemu-system-aarch64 \
  --experimental-address-profile --continue-seconds 5
python3 tools/run_spark.py --qemu build/qemu-spark/qemu-system-aarch64 \
  --experimental-address-profile --rot-error-replies --continue-seconds 5
```

The first command verifies SEC/DXE entry on stock QEMU. Both continuations
currently return nonzero for their firmware exceptions. The error-reply
scenario additionally reaches `D3`/`90`. Per-run evidence is local under ignored
`firmware/runs/<UTC timestamp>/`. No host disks or networking are attached.

Next work is secure-world/MM communication, flash/NVRAM, and the remaining
architectural protocols, followed by boot-device discovery and the console.
Physical inventory alone cannot establish those service contracts.
