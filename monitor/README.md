# Spark firmware lab monitor

This directory contains original Apache-2.0-licensed source. It contains no NVIDIA,
MediaTek, or AMI firmware code. `tools/build_monitor.py` builds it with Clang,
LLD and LLVM objcopy into ignored `build/monitor/`.

The monitor starts at EL3, boots the unchanged Spark Standalone MM package at
`0x93a40000` in S-EL1, and then enters unchanged Spark UEFI at `0x80800000` in
NS-EL2. A small S-EL2 dispatcher receives MM's HVC calls. EL3 switches between
the suspended secure dispatcher and UEFI for MM direct messages. General,
FP/SIMD, translation and basic EL1/EL2 context are saved per world. The runner
disables SVE, SME and pointer authentication; this context implementation does
not support those extensions or multiple CPUs.

Supported FF-A operations are the pinned image's version/identity discovery,
one-page RX/TX mapping, MM partition discovery/release, direct MM requests,
MM wait/responses, and one bounded synchronous flash memory share/reclaim.
Normal-world sharing with STMM has a separate bounded share/retrieve/relinquish/
reclaim lifecycle with caller, descriptor, region and permission validation.
See [MM sharing and firmware buffer checks](../docs/mm-sharing.md) for the
supported wire format and the unchanged firmware's fixed-buffer restriction.
Unsupported calls return errors. This is a compatibility subset, not an FF-A
conformance implementation or a replacement for production Hafnium/TF-A.

The real MM firmware implements UEFI variables. `nor.c` supplies its peer flash
interface using a 1 MiB NOR variable partition at flash offset `0x03000000`.
Reads, atomic 1-to-0 programming, aligned block erases and synchronous flushes
operate on guest RAM backing at `0xa0000000`. The runner loads and atomically
saves that backing in ignored `firmware/state/`. It never connects a physical
flash device. No variable protocol is injected into UEFI.

A separate modeled clock service supplies the PMIC RTC register read requests
and RTC set-time request used by the real UEFI clock driver. It starts at
2000-01-01 and advances with QEMU's architectural counter; it is independent
of host time. Alarm and unrelated PMIC services return unsupported. Clock state
is per process; persistent UEFI variables are independent of the clock.

SMCCC TRNG 1.0 discovery and 32/64-bit random requests are backed by QEMU's RNDR
instruction, which obtains entropy from the host CSPRNG. The service uses its
own lab UUID, checks request sizes and clears unused bits. Missing RNDR and
entropy failures return errors. This allows Spark's real RngDxe to install the
EFI RNG protocol required by its network stack. No fixed seed or substitute
UEFI RNG protocol is used; this is not execution of vendor TRNG firmware.

The synthetic loader reserves the monitor/stacks, MM/package/page-table and
communication memory, and the NOR backing. Boot SRAM and board/fuse values in
the QEMU profile are approximations. No secure stage-2 isolation, authenticated
boot, attestation, production security service, firmware update, CPU power
management, migration, or warm-reset support is claimed. Run only as a local
firmware research lab. Use a new QEMU process for each cold boot.

The synthetic integration test executes original S-EL1 and NS-EL2 guests,
checks MM discovery and bad mapping rejection, makes two world switches,
checks FP/SIMD and thread-register preservation, TRNG discovery/bounds/bit
packing/nonrepeating requests, and timer PPI 30.
An additional synthetic guest pair exercises NS memory sharing, RX ownership,
retrieval and reclaim alongside a concurrent secure flash transaction.
Native tests exercise the exact `nor.c` used by the monitor. CI has no vendor
inputs and publishes no firmware or execution captures.
