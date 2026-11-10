# Next four items: plan and state (2026-11-08)

Four gaps were left open after the flash work. This is the plan for closing them, and
what happened when it was executed. It was designed with an external review (one pass over
the code; the failure modes below are theirs) and then implemented item by item on top of
the merged branch.

**State at the end:** items 1–3 are implemented and verified; item 4 could not be closed
from the artifacts that exist and is recorded as a negative with its evidence. The suite
is 146 tests (20 skipped) and the eight gated boot cases pass, including a code-volume
edit reaching the console and a programmed page surviving a watchdog reset. Everything
here is in the follow-up series; nothing in it changes what an existing receipt means,
except that a receipt now records which layout and which program model produced it.

## 1. First use on a clean clone — done

**Problem.** A fresh clone could run the fast tests but the boot cases skipped with a
message that did not say what was missing, and nothing in the repository produced a
store a parser could read without vendor bytes.

**Change.**
- `tests/store_fixtures.py` — a generator: an erased part with the volume header the
  real store actually carries (erased, length at `0x20`, `_FVH` at `0x28`, header length
  `0x48`), and NVAR records packed from `0x60`, in one or both banks. It builds the
  *format*, never a substitute for a captured store whose boot outcome was measured.
- `tests/test_store_fixtures.py` — the generator's own tests: the header matches the
  measured shape byte for byte, records parse in both banks with their payloads, a clean
  fixture has no poison findings, the inspector accepts it.
- `tests/test_store_behaviour.py` — skip messages now name the missing path and say that
  preparing the vendor release does not create the lab's captures.
- `README.md` — a first-use sequence, and a correction: warm reset *is* modelled now.

**Why no checksummed EDK2 header.** The real store's volume header is erased apart from
the length and `_FVH`; a fixture that built a proper checksummed FV header would be
stricter than the format, and a validator that demanded one would reject the real thing.
Measured, not assumed.

**Naive failure avoided.** Committing generated `.bin` fixtures would fail
`check_publication.py`; the generator is source, the tests build into temporary files.

## 2. The board's layout wherever the input is one — done

**Problem.** The whole-device layout was opt-in, so the common case stayed the host-file
layout — the one where a code write proves nothing.

**Change.** `resolve_flash_layout(state_size, layout, board, code_offset, nor_size)`:
- `auto` (default) reads the input's own size. 64 MiB is a whole device (offset `0`,
  code at `0x2000000`); 32 MiB can only be a store (offset `0x03000000`).
- `whole` and `store` force it; `--board` refuses `store`.
- A whole-device run with no state file builds one from the prepared release
  (`fresh_image()`: erased store in the measured shape + the release's code volume).
- A 32 MiB file asked for as `whole` is refused with the hint to pass
  `--flash-layout store`, rather than booted as something it is not.
- The record gains `receipt_schema: 2` and `flash_layout`, so a receipt says which
  layout produced it.

**Why not flip the default outright.** Changing what a *file means* by changing a
default would reinterpret every existing receipt. Size is unambiguous, so inferring from
it changes nothing that already exists and gives the board layout to every new image.

**Tests.** Seven resolution cases (32 MiB, 64 MiB, absent, explicit zero offset, forced
legacy, board+legacy refused, unknown layout) plus boots: a 32 MiB store still boots
store-only, a 64 MiB image boots whole-device automatically, an absent state under
`--flash-layout whole` is created and boots to setup.

## 3. Torn page: a reset inside a program pulse — done

**Problem.** The model committed a whole page and then spent the page's time, so a reset
could never leave a partly written page — the state a power loss leaves.

**Change.** `--program-model {page,byte}`, recorded as `page-before-delay-v1` or
`linear-request-byte-v1`:
- `page` — the old model, unchanged; default for the store-only layout.
- `byte` — each physical page's whole budget is spread over the bytes that pulse covers,
  and each byte is committed when its share is spent. Deadlines are cumulative and
  rounded up, so 400 µs is never spent as 400/256 = 1 µs, and a short write still pays a
  full page budget. Default for the whole-device layout.

**Tests.** `tests/fixtures/nor_cut.c` supplies the clock and cuts the pulse at an exact
microsecond by `longjmp`ing out of the real `nor.c`: a cut at zero commits nothing, a cut
mid-page leaves a prefix, a full pulse commits 256 bytes and spends exactly 400 µs, and a
two-byte write across a page boundary spends 800 µs.

**Honest limit.** Linear progress inside a page is an approximation of how the die
settles, and a watchdog reset does not remove flash power. The model says which version
produced a result so a claim can be qualified.

## 4. The board's watchdog period — searched, not established

**Problem.** The model's reboot cycle is its own (~60 s); the board's arming value is
unknown.

**Plan.** Find the arming path in the pinned firmware, not by inference:
- the watchdog driver in the UEFI image and its `SetTimerPeriod` callers, located with
  `tools/module_map.py` and read with a disassembler;
- the control-frame writes at `0x1c042000` (WOR/WCS) and refreshes at `0x1c041000`,
  including constructed addresses and any conversion from milliseconds;
- if the UEFI image does not arm it, the standalone MM image and the predecessor blobs
  actually present in the capsule;
- record artifact hashes, the module GUID, the call site, the reconstructed WOR, the
  clock and frequency, the enable/refresh sequence, both stage intervals, and how it was
  derived.

**What must not be done.** Preloading a competitor when the firmware already programs
WOR; claiming a period from a reboot's duration or from a matching literal; treating
WS0 (interrupt) and WS1 (reset) as one stage — an unrefreshed reset is the *second*
expiry.

**Outcome: a recorded negative.** `research/watchdog-periods.json` carries the artifacts,
their hashes, the searches and what each returned:

- `uefi.fd` (2.155.11): `0x1c041000` and `0x1c042000` occur exactly once each, adjacent,
  inside module `7e374e25-…` at image offset `0x6bdc90` — platform description data, not a
  code constant. "Watchdog" appears only in HII and protocol-name tables; no driver that
  programs the interval was found.
- `stmm.pkg` and `socfw.cap`: neither address present in the artifacts as stored. The
  capsule is a vendor container whose predecessor stage has not been unpacked, so an
  arming path inside it would be invisible to this search.
- Measured behaviour, from earlier hardware work: the interval was armed before the
  firmware ran (a payload read the status back as armed, and the firmware's own enable
  writes the control bit without writing the interval).

So the period stays a model value, and the model does not preload one. What would close
it: unpack the capsule's predecessor stage, or read the interval register from a live
unit before any payload runs. `tests/test_watchdog_record.py` refuses the record if it
ever claims a value without a derivation, and re-runs the searches where the artifacts
exist.
