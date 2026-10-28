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
| Vendor serial output with custom profile | `Checkpoint 61` |
| Firmware console / setup menu / OS boot | Not achieved |

The DXE allocation is deterministic for this image and handoff. The runner
fails if the exact checkpoint is not reached; it records the PC before and
after each single-step. A translated instruction trace alone does not count
as execution evidence. The default command stops after the two entry checks.

## Current blocker

After checkpoint `61`, `SaveApplLog` loads at `0x86781000`. It faults at PC
**`0x86782350`**, writing **`0x180100c0`**, with data-abort ESR **`0x96000050`**.
The serial console reports a synchronous exception and the exception handler
enters a dead loop. This MMIO interface and its request/acknowledgement behavior
are not modeled. Treating the region as generic PCI configuration does not
solve the protocol: the firmware then polls indefinitely. That experiment is
not included in the committed profile.

The profile uses QEMU's existing GICv3 and 16550 models at observed firmware
addresses, plus GPIO/pad configuration banks with value/SET/CLEAR readback.
External GPIO state, GPIO interrupts, electrical effects, and clock effects
are approximations or unimplemented. The UART interrupt route and generic
PCI topology are not verified against hardware. No GB10 GPU or secure
partition execution is provided.

## Reproduce

```sh
python3 tools/run_spark.py
python3 tools/build_qemu.py
python3 tools/run_spark.py --qemu build/qemu-spark/qemu-system-aarch64 \
  --experimental-address-profile --continue-seconds 5
```

The first command verifies SEC/DXE entry on stock QEMU. The last continues
through serial checkpoint `61` and currently returns nonzero for the later
firmware exception. Per-run evidence is local under ignored
`firmware/runs/<UTC timestamp>/`. No host disks or networking are attached.

Next work is to establish the `0x180100c0` interface contract and model its
observed transactions, then identify the next required flash, secure firmware,
and platform services. A matching hardware inventory remains outstanding.
