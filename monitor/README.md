# Spark firmware lab monitor

This directory contains original Apache-2.0-licensed source. It contains no NVIDIA,
MediaTek, or AMI firmware code. `tools/build_monitor.py` builds it with Clang,
LLD and LLVM objcopy into ignored `build/monitor/`.

The monitor starts at EL3, boots the unchanged Spark Standalone MM package at
`0x93a40000` in S-EL1, and then enters unchanged Spark UEFI at `0x80800000` in
NS-EL2. A small S-EL2 dispatcher receives MM's HVC calls. EL3 switches between
the suspended secure dispatcher and UEFI for MM direct messages. General,
FP/SIMD, translation and basic EL1/EL2 context are saved per world, including the
optional TCR2, stage-1 PIR/PIRE0 and HCRX state. This QEMU model keeps one copy
per EL, not per security state: leaking Linux's PIE controls into STMM changes
how its legacy page descriptors are interpreted. Optional register support is
discovered once on the profile's `max` CPU (which supplies MMFR3); the secure
state is restored without disabling the normal world's features.
The runner disables SVE, SME and pointer authentication; this context
implementation does not support those extensions or concurrent world switches.
The permission-state regression does not cover GCS (GCSCR/GCSCRE0/GCSPR),
fine-grained trap registers, SCTLR2 or every optional timer/debug/PMU group;
their independent preservation remains unverified. SCR access enables are
not evidence of saved context. See the
[remaining platform boundaries](../docs/boot-status.md#remaining-platform-limits-and-evidence-boundaries).

Supported FF-A operations are the pinned image's version/identity discovery,
one-page RX/TX mapping, MM partition discovery/release, direct MM requests,
MM wait/responses, and one bounded synchronous flash memory share/reclaim.
Normal-world sharing with STMM has a separate bounded share/retrieve/relinquish/
reclaim lifecycle with caller, descriptor, region and permission validation.
See [MM sharing and firmware buffer checks](../docs/mm-sharing.md) for the
supported wire format and the unchanged firmware's fixed-buffer restriction.
Unsupported calls return errors. This is a compatibility subset, not an FF-A
conformance implementation or a replacement for production Hafnium/TF-A.

PSCI_FEATURES advertises the implemented SMCCC_VERSION call, which returns
exactly SMCCC 1.2 (`0x00010002`), so Linux can enable its register conduit.

`boot_spark.py --ffa-ec` (or `build_monitor.py --ffa-ec`) opts into a separate
Linux-facing EC packet model, compiled as `SPARK_FFA_EC`. Without this option
the existing FF-A discovery is unchanged. The option answers FF-A RX/TX and
direct-request feature queries and publishes a 24-byte descriptor for UUID
`78b04d80-d21d-4986-8acb-467b60247ac5`, endpoint `0x8003`, one execution context,
properties `0x0109`. Null-UUID discovery returns this service and STMM;
count-only queries do not acquire RX, ordinary queries require mapping and
release, and the firmware's existing STMM UUID-filtered path is retained.
Register-based discovery and notifications remain unsupported.

Both DIRECT_REQ2 and the legacy 64-bit direct request serve packet submit
(`1`) and cached completion poll (`2`), with operations capabilities (`1`),
read lower clamp (`4`), write lower clamp (`5`), and telemetry (`7`). Invalid
operation/length combinations return a nonzero submit status without changing
the floor. Completion is synchronous, not a simulation of a physical mailbox.
The unset floor is `0xffff`. Published RPM is an **estimate**, computed with the
recovered EC equation `min + (duty - 14) * (max - min) / 86`, zero below 14%
duty, bounded at 100%; CPU limits are 1260..9000 and rear limits 1890..13500.
For a set floor the model applies that equation's inverse independently to each
fan, clipping nonzero requests to its range. Zero explicitly stops both fans.
Duty is rational percent, preserving `forward(inverse(clipped floor))` exactly
without asserting an undocumented physical PWM resolution. This inverse is
arithmetic, **not a recovered EC floor-to-duty controller**. For unset floors,
14% duty and the minimum estimates are an explicit model baseline, not execution
of the firmware's automatic thermal curve.
`MONITOR_TRACE` logs the clamp, duty numerator/denominator in percent, estimate,
and whether the duty came from inverse arithmetic or the unset model baseline.
The equation and ranges come from recovered EC code; no physical tachometer
value, thermal policy, ramp/slew, zero-tach suppression, or >5% republish timing
is simulated. The OEM/AML fan path and its DSDT arithmetic are unchanged.

The real MM firmware implements UEFI variables. `nor.c` supplies its peer flash
interface using a NOR variable partition at flash offset `0x03000000` (or `0x0`
when booting a whole-device `--flash-image`), sized by `NOR_SIZE` (32 MiB
default, 64 MiB for the board's part, 1 MiB for legacy fixtures). Because QEMU
re-applies `-device loader` blobs on reset, `0xa0000000` holds the read-only
seed window, `0xa0000000 + NOR_SIZE` holds a 4 KiB state page, and
`0xa0000000 + NOR_SIZE + 0x1000` holds the live window that the monitor copies
once on first boot and serves across resets. Reads, 256-byte page programming,
4 KiB sector erases and synchronous flushes operate on that live window, with
optional `NOR_WRITE_US` / `NOR_ERASE_US` delays; when `SPARK_CODE_OFFSET` is set,
the monitor also copies the code volume from the live window to `0x80800000` on
each reset. The partition is behind the board's latch: command 8 arms it for a
trusted sender, and a non-trusted request is refused with status `0x0A` once it
is armed. The runner loads the seed and atomically saves the live window in
ignored `firmware/state/`. It never connects a physical flash device. No
variable protocol is injected into UEFI.

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
checks legacy secure executable mappings against NS permission-indirection
leaks and preserves NS TCR2/PIR/PIRE0/HCRX state, FP/SIMD and thread registers.
It also checks TRNG discovery/bounds/bit packing/nonrepeating requests and timer PPI 30.
An additional synthetic guest pair exercises NS memory sharing, RX ownership,
retrieval and reclaim alongside a concurrent secure flash transaction.
Native tests exercise the exact `nor.c` used by the monitor. CI has no vendor
inputs and publishes no firmware or execution captures.
