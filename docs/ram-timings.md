# RAM timings

## The correction this page needed

Earlier text here said the three partitions were "erased in every artifact". That was wrong about
`BCT`, and the reason is a coordinate system. The component map's offsets are relative to **where the
MediaTek image header sits**, not to the start of the payload and not to the file: in the 2.158.15
capsule the header is at slot offset 0x203000, so `BCT` — recorded at 0x1992000 — is at slot offset
0x1b95000, i.e. capsule file offset 0x1b95c5f (the file holds the slot image after 0xc5f of FMP,
certificate and MSS1 wrapper). Read literally, the region at 0x1992000 is empty; at the right place it
is **24.9% filled**. The correction was first recorded outside this repository and re-verifies on
this capsule.

**Everything below is in slot offsets.** `tools/container.py` derives the base by locating the
`AAAA` header rather than assuming 0x203000, and `research/bct-patch.json` names its coordinate space
for exactly this reason.

What the filled `BCT` actually contains, parsed and recorded in `research/bct-patch.json`:

```
BCT patch, version 1, SHA-256 over the payload
  DRAM_ENCRYPT   values 0 0 0 0        (off)
  TPM            values 0 0 2 2
  PLUTON         values 0 0 1 1
  PLUTON_DRTM    values 0 0 1 1
```

Four named switches with a digest — **a patch, not the DRAM training table**. There is no timing, no
frequency and no voltage level in it. `DDRFSK` and `MISC` are empty in the capsule, and the recovery
media's part image has all three erased, which is consistent: an update capsule patches the device's
existing `BCT` rather than shipping a whole one.

So the honest statement is narrower than "there are no timings anywhere": **nothing we hold carries a
training table, and the capsule's `BCT` is a four-switch patch.** A timing change still needs the
device's own `BCT`/`DDRFSK`, i.e. a readback.

on the DGX Spark: where they live, and what we have

**Status:** the DRAM *training tables* are not in any artifact we hold; the capsule does carry a `BCT`
**patch** of four named switches (see above), which is not a timing table. This page records what is
known, what is missing, and what would change the answer.

## Where they would be

The vendor capsule's map names the partitions that carry boot configuration and DRAM data:

| partition | size | offset in the table | slot offset (header 0x203000 + table) | capsule file offset (slot + 0xc5f) |
| --- | --- | --- | --- | --- |
| `BCT` | `0x4000` | `0x1992000` | `0x1b95000` | `0x1b95c5f` |
| `DDRFSK` | `0x160000` | `0x1996000` | `0x1b99000` | `0x1b99c5f` |
| `MISC` | `0x1000` | `0x1af6000` | `0x1cf9000` | `0x1cf9c5f` |

The base matters: reading the capsule at the image offsets finds the file's other bytes, not the
partitions, which is how the first version of this page concluded "erased".

`tools/timing_tables.py <image>` reports, per artifact, whether those regions are populated and, if
so, extracts them with hashes — the comparison a timing experiment or a two-unit diff would need.

## What is missing

What each artifact actually carries, measured:

- **the capsule** (`socfw.cap`, the newest release): `BCT` **24.9% filled** — the four-switch patch
  parsed above; `DDRFSK` and `MISC` empty;
- **the recovery media's part image** (`spirom/*.bin`, 2 × 32 MiB slots): all three regions `0xff`,
  and the first MiB of slot 0 is header data, not a BCT.

That combination is coherent for an update path: a capsule patches the device's existing `BCT` instead
of shipping a whole one, and the recovery media carries no board-specific configuration at all.

So: **the `BCT` patch is everywhere, and the timing set is nowhere.** A parser for the patch format
exists (`research/bct-patch.json`, `tests/test_bct_patch.py`); a parser for timings would still be a
guess about a layout nobody has seen, because `DDRFSK` is empty in all six artifacts. What *can* be stated is narrower: **no artifact we hold carries timings
or a full BCT**, which is not the same as "timings cannot be changed" — a device's flash has the real
partitions, and the capsule proves the format is reachable in principle.

## What would change the answer

1. a read of a unit's flash part at the offsets above (no boot needed, and the bricked unit's
   regions are untouched by the store failure);
2. or a vendor/factory image that ships those partitions populated;
3. then `timing_tables.py --extract` writes them out, and a parser is built against the real table.

## What can be read without them

On a running unit, the *effective* configuration is observable even when the tables are not:
`/sys/devices/system/edac` (if EDAC is enabled), the memory controller registers through
`/dev/mem` where the kernel allows it, and the SMBIOS type 17 entries that describe the installed
parts. Those describe what the boot chain *did*; they do not let it be changed.

What this does not claim: that the timings are fixed in silicon. They are set by the boot chain; the
tables that set them are simply not in the hands of this repository yet.

## What the firmware says it can be built with

The vendor UEFI carries a build-configuration vocabulary — NUL-separated knob names, each with its
legal values — and four of them are DRAM:

```
DRAM_ENCRYPT     0:off, 1:on
DRAM_VOL_LEVEL   0:NV, 1:HV, 2:LV, 3:6P, 4:7P, 5:8P, 6:9P, 7:10P
DRAM_RAPIDK_DIS  0:on, 1:off
DRAM_EYESCAN     0:non TPM, 1:fTPM, 2:dTPM
```

`research/dram-knobs.json` records all 37 knobs with the table's offset and the image's hash, and
`tests/test_dram_knobs.py` re-derives them from the image so the record cannot drift. Two things this
does and does not mean:

- **It names the levers a memory change would use.** `DRAM_VOL_LEVEL` is a level selector with eight
  settings (four package classes and their "P" variants), and `DRAM_RAPIDK_DIS`/`DRAM_EYESCAN` are
  training toggles — the vocabulary a build-time timings change would be expressed in.
- **It is not a reachable interface.** The table is a schema compiled into the image; no variable in
  the firmware's own list (73 names, read under the emulator) carries DRAM settings. Of the three
  partitions that would carry the resulting timings, `BCT` carries a four-switch patch (above) and
  `DDRFSK` and `MISC` are empty — in all three capsules and in both slots of the recovery media's
  part image. The empty regions are checked byte for byte, not by sampling a prefix.

**What the flash transport itself says (2026-11-10).** The read command takes a **32-bit** address and
copies out of the window at `0x50000000`, so the three partitions below are inside its range; the 16 MiB
ceiling belongs to the program/erase helper alone. The obstacle is not the address, it is the gate: once
the trusted endpoint arms the lockout at `ReadyToBoot`, a caller that is not that endpoint is refused.
Reaching the partitions therefore needs a caller that runs before that point — which is a property of the
firmware's boot order, not of this repository.

So the answer to "how do I change the Spark's RAM timings" is unchanged and now better founded: not
from any artifact we hold. What would change it is a readback of those three partitions from a live
unit, which is the same prerequisite the store work has.
