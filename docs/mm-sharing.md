# Normal-world memory sharing and STMM communication

The lab monitor supports a bounded normal-world-to-STMM FF-A memory-sharing
transaction. The original UEFI and STMM images are unchanged. A successful
`FFA_MEM_SHARE` does **not** extend STMM's permitted MM communication buffers.

## What the pinned firmware actually accepts

Verified locally on 2026-10-31 with SoC 2.158.15's unchanged STMM package:

1. The real firmware reports a normal-world communication buffer at
   `0x933e0000`, size `0x20000` (128 KiB).
2. An MM communication request using that buffer succeeds.
3. The monitor accepts a valid share of a separate page at `0x88002000` with
   STMM as its recipient. The share remains active during the next checks.
4. The same MM request using that outside page is rejected by STMM itself.

The pinned manifest describes `stmmns-memory` at the reported base, with
32 pages and `nv-non-secure-memory` set. The lab loader reserves this carve-out
so normal UEFI allocations do not consume it. Other addresses in the reserved
MM/package/page-table range are not accepted as NS-owned share buffers.

For this pinned image, the direct-request register convention is:

| Request | Registers |
| --- | --- |
| Discover NS buffer | `x0=0xc400006f`, `x1=0x8004`, `x2=0`, `x3=0xc0270001`; remaining arguments zero |
| Successful discovery | `x0=0xc4000070`, `x3=0xc4000061`, `x5=base`, `x6=size` |
| MM communication | `x0=0xc400006f`, `x1=0x8004`, `x2=0`, `x3=0xc4000041`, `x4=0`, `x5=buffer`, `x6=0`, `x7=0` |

The communication buffer contains an EFI MM communication header and payload.
Clients should discover the firmware's buffer and use its protocol; arbitrary
allocated pages do not become MM communication buffers merely by being shared.
This is a partition-specific restriction in addition to FF-A sharing semantics.

## Implemented sharing subset

The monitor binds the owner to normal-world endpoint `0` and the borrower to
STMM endpoint `0x8004`. It supports one active NS transaction, separately from
the existing synchronous STMM-to-flash transaction and its handle `1`.

| Operation | Supported behavior |
| --- | --- |
| `FFA_MEM_SHARE`, `0x84000073` / `0xc4000073` | Copy and validate the descriptor from the mapped NS TX page; return a fresh handle |
| `FFA_MEM_RETRIEVE_REQ`, `0x84000074` / `0xc4000074` | STMM-side retrieval through its TX page; return the stored descriptor in its RX page with the NS attribute |
| `FFA_RX_RELEASE` | Release the RX mailbox after reading the descriptor; this does not relinquish the share |
| `FFA_MEM_RELINQUISH`, `0x84000076` | Borrower releases its retrieved reference |
| `FFA_MEM_RECLAIM`, `0x84000077` | Original owner reclaims after the borrower has relinquished, or before any retrieval |
| `FFA_FEATURES` | Discover the implemented memory operations and retrieve-response NS attribute support |

The descriptor uses the FF-A v1.1 layout: 48-byte header, one 16-byte endpoint
access descriptor and one contiguous constituent; 96 bytes for sharing and
64 bytes for retrieval. Shares are page-aligned, at most 32 pages, with normal
write-back/inner-shareable memory attributes, RO/RW data permissions and no
executable access. Retrieval cannot increase the granted data permissions.
Clear, time-slice and other optional transaction flags are not implemented.

Validation rejects sender spoofing, unexpected recipients, malformed offsets,
reserved fields, zero/excessive page counts, wrapping/out-of-RAM addresses,
monitor/NOR/secure carve-outs and mailbox overlap. Descriptor copies remain
valid if the caller edits or unmaps its TX page. New mailbox mappings cannot
overlap an outstanding shared region. A full RX mailbox is not overwritten.
Handles are not recycled, and reclaim cannot succeed while the borrower still
holds a retrieved reference. Failed requests do not replace the active share.

Fragmentation, multiple borrowers/constituents, `FFA_MEM_LEND`, donation and
device/executable memory are unsupported. This remains a compatibility subset.
The lab has no secure stage-2 isolation; descriptor permissions and ownership
checks do not establish production memory isolation. No STMM page tables,
communication-buffer checks or successful service replies are patched.

## Reproduce and interpret the evidence

```sh
SPARK_QEMU=build/qemu-spark/qemu-system-aarch64 python3 -m unittest tests.test_ffa_memory tests.test_monitor -v
python3 tools/probe_mm.py
```

The synthetic tests use no vendor firmware. They cover descriptor validation,
permissions, stale handles and the complete SMC/HVC sharing lifecycle, including
an independent flash-service transaction while the NS share is active.

The local-only probe boots the real STMM with an original bare-metal NS client
in place of UEFI. It uses a fresh disposable NOR image, not the saved UEFI
variable store. The client discovers the buffer, registers the outside share,
verifies the valid request and attempts the outside request. The host pauses
at the real firmware's HVC instruction and records the rejection it emits:
`x0=0x84000060`, `x2=0xfffffffffffffffd`, `x3=0xc4000041`, `x5=0x88002000`.
The `-3` here is the firmware's SPM-MM return convention; it must not be
confused with FF-A's separate error-code definitions.

The negative probe stops before that error HVC executes. It verifies the
firmware's rejection, not delivery of that error back to an NS caller; the
lab's existing dispatcher does not complete this firmware error path. It
does not claim a fix for clients that pass unapproved MM pointers.

Logs, input hashes, generated client and register evidence stay under ignored
`firmware/runs/<UTC>-mm-buffer/`. It captures no full RAM dump. Setup and native
PXE checks remain separate full-firmware regressions in `tools/boot_spark.py`.

The protocol model follows [FF-A memory-sharing semantics](https://hafnium.docs.trustedfirmware.org/en/latest/secure-partition-manager/secure-partition-manager.html#memory-sharing).
NVIDIA's [published Hafnium entry code](https://github.com/NVIDIA/edk2-nvidia/blob/cb638768ec174bb2041f87569409c53e4af4292e/Silicon/NVIDIA/StandaloneMm/Library/StandaloneMmHafniumEntryPoint/AArch64/StandaloneMmCoreEntryPoint.c)
provides context for the buffer-discovery and MM request conventions. The
local execution of the pinned image is the evidence for the results above;
upstream source and the FF-A standard alone do not prove the exact behavior
of Spark's production SPMC. That SPMC is not executed by this lab.
