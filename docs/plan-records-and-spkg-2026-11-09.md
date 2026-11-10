# Plan — close the extraction loop, read the SPKG containers, and prepare the one hardware packet

Written after the extraction defect was found, fixed and reviewed three times (`46deb86`…`d05db9f`).
The extraction is right; what remains is everything that inherited the wrong reading, plus the parts
of the original goal that no artifact can settle.

Each phase states what it changes, how it is verified, and what "done" means. Nothing here touches
hardware, and nothing here publishes.

## Phase A — every record that inherited the wrong slice

**A1. `research/spbm-interface.json`.** It was derived when extraction was off by 0x203000. Its DSDT
facts (`Memory32Fixed`, `_CPC` at base + 0x5C404, the register layout) do not depend on extraction and
stand. Anything that came from a component slice must be re-derived or withdrawn, field by field, with
the old value kept as `invalidated_by` so the change is visible rather than silent.

**A2. Audit every file in `research/`** for (i) fields derived from a capsule or part-image slice,
(ii) the coordinate space those fields are in, (iii) the artifact hash they came from. A record that
cannot say which artifact and which coordinates produced a number is not evidence.

**A3. A checker test** (`tests/test_record_provenance.py`) that walks the records and fails when one
carries slice-derived measurements without `source.artifact` and a coordinate statement. This is the
mechanical guard: the defect recurred four times as a *claim outliving its state*, so the fix is a test,
not another sentence in a doc.

**Verification:** every changed number re-derived from the artifact in the test itself; the suite green;
each record states artifact hash + coordinate space + (where relevant) the header position.

**A4 (from review).** The guard must cover *text*, not only numbers: "erased in every release" survived
four rewrites as a fragment. `tests/test_record_provenance.py` now fails on the phrase families unless
the surrounding text is a retraction, and it asserts hash + coordinate space per slice-derived record.
Old values stay as `old_value` with `invalidated_by` naming the correction, so history is readable
without being active evidence.

**A5 (from review).** Fix the *active* sentence, not by appending another correction: the coordinate
table in `docs/ram-timings.md` labelled a slot offset as a capsule-file offset. Done.

**Done when:** every active slice-derived claim either replays against its named artifact or is
explicitly withdrawn; the private mirrors contain no surviving contradictory claim (checked, not
assumed).

## Phase B — what the SPKG containers actually are

`STMM`, `FLASH` and `ESPI` each begin with `SPKG` version 2 at the body offset their own descriptor
chain gives (0x2b0), and each has an FDT at +0x12b0 whose structure block is empty after the root node.
"SPKG present, tree empty" is where the last round stopped; it is not an answer.

**B1. Parse the SPKG v2 header and manifest**: magic, version, declared lengths, count, and whatever
identifies the package (UUID, name, image list). Bounded parsing with explicit refusals — no scan for
a pattern and call it a name.

**B2. Record the result honestly** in `research/spkg-manifest.json`: fields that are understood, fields
that are opaque by offset and length, and whether anything in the package names the service it
implements. For `ESPI`/`FLASH` this is the only remaining chance to describe the flash-service gate
from the capsule.

**B3. Test it** against the capsule: the parsed header must reproduce the component's extent, and the
test must fail if the magic, version or length fields move.

**Verification (from review):** the body offset is derived from the component's descriptor chain, never
the literal 0x2b0; extents are reconciled — the package's declared ranges lie inside the component, the
manifest is followed by page-aligned padding, and the image is not required to equal the CBOR-wrapped
size; truncation, an unsupported version, out-of-bounds extents and a manifest that names nothing are
refused. The record states `inspection_status` and says what a label does **not** establish (a partition
id is not a transaction contract).

**Done when:** the record says either what the package names, or that it names nothing — with the
offsets that decided it.

## Phase C — the one thing that needs hardware, prepared and not run

RAM timings, GPU/core clocks and the DRAM training set need `BCT`, `DDRFSK` and `MISC` **from a live
unit**. The capsule carries a BCT patch and two empty regions; that is measured and will not change.

**C0 (from review).** The packet lives in an ignored, durable directory outside anything the public
tree publishes, and Phase E's mirroring excludes it.

**C1. Draft the read-only packet**: one payload, one boot, zero writes — read the three regions and the
programmed controller state, emit a digest and the bytes, exit. Local-only material; the publication
rule for flash readback stands.

**C1a (from review).** An allowlist of side-effect-free reads only: refuse a denied read, an unknown
mapping, a short read or an unsupported register — never fall back to gate manipulation or arbitrary
MMIO. Capsule coordinates do not authorise a read at a physical address; the address must be justified
from a manifest region or a documented register map.

**C2. Presubmit it** (`scripts/presubmit.py`) so the payload is known-good before any coordinator time
is spent, and record the exact submission line in the packet README.

**Not in scope without an explicit go:** submitting it, running it, publishing anything it returns.

**Done when:** the packet exists, presubmits clean, states its authorisation requirement, and has not
been submitted.

## Phase D — publication audit of what is already shippable

The fan control is complete and verified (ACPI path on ACUM028, `tools/fanctl.py`, built module, the
sp8 measurement recorded). The emulator fork already carries it.

**D1. Audit the fork tree** for private paths, host paths, firmware payloads and lab-repository names,
and for anything that describes flash readback. `tools/check_publication.py` covers artifacts; the audit
covers text.

**D2. Confirm the fan package stands alone**: docs, tool, tests, and the record of what a level means
(a pin, reboot restores). Fix gaps rather than advertise.

**Done when:** the audit is clean and reported, or the gaps are closed.

## Phase E — close

Both trees green, records mirrored into the lab notes, commits pushed to `fork/flash-fidelity-followup`,
lab `main` committed. Report: what changed, what is measured vs inferred, and what still needs the owner.

## What this plan deliberately does not do

- It does not try to decrypt anything, and does not claim a timing table can be recovered from the
  capsule.
- It does not run anything on a Spark.
- It does not publish flash/readback material in any form.
- It does not add a test that merely restates a constant: every new test re-derives from the artifact.
