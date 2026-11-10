# Power limits and clocks: what the firmware advertises, and what is reachable

**Status:** the interface is mapped from the vendor's own DSDT; the standard OS lever (CPPC) is
confirmed present; the vendor power-limit region's **base address is not known**, so nothing is
written.

## The standard lever: CPPC

Every CPU device in the DSDT is a standard `ACPI0007` processor with a `_CPC` object (Continuous
Performance Control) — 20 of them, one per core. Linux exposes that as `cpufreq` (the `cppc_cpufreq`
driver), which is the supported way to move a core's performance request: scaling governor, energy
performance preference, and the min/max performance limits.

That path needs no vendor region and no kernel module, and it is where "speed up the cores" starts.

## The vendor lever: SPBM power limits

An ACPI method in the same DSDT returns an interface descriptor naming four SoC blocks and every
field inside each one. `research/spbm-interface.json` holds all 212 fields. The ones that matter for
a power experiment are the limits, in four sources so you can tell who asked for what:

| field | offset | meaning |
| --- | --- | --- |
| `SPBM_PL1_VAL_OS_OFFSET` … `PL4` | `0x100`, `0x104`, `0x108`, `0x10C` | what the OS requested |
| `SPBM_SYSPL1_VAL_OS_OFFSET` … | `0x110` … | the same, for the system-level limit |
| `SPBM_PL*_VAL_EC_OFFSET` | `0x120` … | what the EC asked for |
| `SPBM_PL*_VAL_UEFI_OFFSET` | `0x140` … | what UEFI set |
| `SPBM_PL*_VAL_OFFSET` | `0x160` … | the value actually in effect |
| `SPBM_PL1_KP/KI/KD/TAU_OFFSET` … | `0x200` … | the controller's PID terms per limit |

`CPUEB` fields add the CPPC interface offset (`0x5C404`), the PL interface (`0x5B400`) and the
fast-DVFS and performance-counter logs, plus two TJPID physical addresses (`0x0539A000`,
`0x0B39A000`).

## What is missing, and why it matters

The table names **offsets inside** a shared structure; it does not state where that structure lives.
The base comes from the block's own driver (CPUEB/SPBM), and no artifact in this repository states
it. Writing `0x100` into an assumed base would be a shot in the dark at a live machine's memory, so
it is not done here — the map is recorded, the base is the thing to obtain.

## What a completed experiment would look like

1. read the effective `SPBM_PL1_VAL_OFFSET` and the OS slot on a running unit (through the CPUEB
   driver, or from firmware if a payload can reach that region);
2. write a higher OS limit, re-read the effective value, and show the two differ by exactly the
   change asked for;
3. measure: does a sustained workload finish sooner, and does the EC/thermal side object
   (`_TMP` from the fan section, which the DSDT already exposes);
4. restore the original value and show the effective one returns.

Steps 1 and 2 are implemented in `tools/pl_limits.py`, which takes the base as an argument instead of
assuming one:

```sh
pl_limits.py --image spbm.bin --read          # the OS slots and the effective ones
pl_limits.py --image spbm.bin --set pl1=45000 # write the OS slot, after backing the window up
pl_limits.py --image spbm.bin --restore spbm.orig
```

The tool reads its offsets from `research/spbm-interface.json`, backs up the window before the first
write, and restores it on request — so an experiment on a real unit is reversible by construction.
Pointed at a device node it refuses without `--base`, because the base is the one thing this
repository cannot supply.

`tests/test_pl_limits.py` runs it against a synthetic window: the offsets, the read, a write that
lands only in the OS slot (the effective value is the firmware's decision, not the tool's), the
backup, the restore, an unknown limit refused, and a device node without a base refused.

**The base address is known.** `\_SB.MTEL` — a `NVDA8800` device in the same DSDT — declares
`Memory32Fixed(0x05170000, 0x00060000)` in its `_CRS`, and the registers the processor `_CPC`
objects return land inside it at the offsets this table names for CPUEB:
`0x5170000 + 0x5C404 = 0x51CC404`, the first CPPC register the DSDT hands back.
`research/spbm-interface.json` records the derivation with its arithmetic, and
`tests/test_power_interface_base.py` checks it; `tools/pl_limits.py --base 0x5170000` is therefore
usable on a live unit. What is still missing is a run that writes through the window on hardware.

The earlier text of this page said the base was stated nowhere. Everything downstream of it —
read the live limits, change one, watch the effective value follow, measure, restore — is one command
each.

## Demonstrated in the emulator

The semantics, not the address, are proven in QEMU. `monitor/spbm.c` holds the documented fields and
serves them on the OEM function id the firmware already forwards (`0xC400008D`, endpoint `0x8003`)
under a lab GUID — a fresh function id never arrives, because the firmware's own SMC filter drops ids
it does not know, which cost a rebuild to learn. A write to an OS limit slot is mirrored into the
effective slot, the one relationship the interface table supports.

```sh
python3 tools/boot_spark.py --pxe-test --board --flash-image firmware/state/board-2158.bin \
    --firmware-version 2.158.15 --pxe-image pl.efi --pxe-marker PL-OK
# B 0        effective PL1 before
# O 45000    OS slot after the write
# A 45000    effective value followed
# R 0        restored
# PL-OK
```

`tests/test_spbm_model.py` asserts that sequence. What it does not assert, and cannot: that SPBM
would honour the same numbers on the board — its firmware ships encrypted (entropy 8.00 in the only
artifact we have, `research/dvfs.json`).

## The GPU partition is not a table either

The `GPU` component is populated (524 612 bytes) and was the last place a readable operating-point
table could have hidden. **The first answer was wrong**, because the extraction was: at the corrected
slice the `GPU` partition holds **no readable container tag at all**, and `PLL` carries one,
`PLL_CLK-1.0.0` — the earlier tag list (`DPM_BPT`, `SPBM_BPT`, `SPM_*`) came from bytes 0x203000 away
from the partition.

What the eight components measured in `research/dvfs.json` share is high entropy (7.9-8.0), which is
consistent with compression *or* encryption and does not distinguish them: the `UEFI` component is
8.00 too and is a decodable LZMA stream. So the defensible statement is the narrow one — **no
operating-point table is readable in those eight** — not "the firmware is encrypted". The components
outside that set measure lower and for their own reasons (`BCT` 0.89, `STMM` 3.76, `FLASH` 5.42,
`ESPI` 5.55, `OSR_FW` 1.98), and the record says so.

So a GPU clock change is not a matter of finding the right table and editing it: the numbers live
inside containers the firmware unpacks, and nothing in the artifact tells us how. `research/dvfs.json`
records the measurement, and `tests/test_dvfs_record.py` re-measures it against the capsule so the
negative cannot quietly become stale.

## The lever the OS already owns: CPPC

Every processor device carries `_CPC` with SystemMemory registers inside the block `\_SB.MTEL`
declares, which means Linux drives CPU frequency itself — no vendor register poke, no module. The
interface it exposes is `/sys/devices/system/cpu/cpufreq/policy*/`:

```sh
tools/cpu_clocks.py status                 # driver, governor, range, current, preference
tools/cpu_clocks.py cap 3000000            # cap at 3000 MHz, saving what was there
tools/cpu_clocks.py epp performance        # where the policy publishes a preference
tools/cpu_clocks.py restore                # put the saved caps back
```

The tool reads every range from the kernel (which reads it from the firmware) and refuses a
frequency above `cpuinfo_max_freq` instead of clamping: the firmware's ceiling is the ceiling, and a
cap this tool did not set must not be reported as if it had. `restore` puts back what the last change
saved, so the experiment is reversible by construction — the same shape as `pl_limits.py`.

What this does not do is raise the ceiling. `cpuinfo_max_freq` is what the firmware advertises;
anything above it is a change to the firmware's tables or the DVFS blobs, and those are the signed
containers recorded in `research/dvfs.json`.
