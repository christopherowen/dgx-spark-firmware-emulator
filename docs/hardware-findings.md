# Read-only Spark hardware findings

Collected on 2026-10-28 from a privately owned DGX Spark using SSH inventory
reads. Raw ACPI tables, kernel logs, device identities, and acquisition metadata
stay under ignored `firmware/hardware/`. Only reviewed address facts are
published here. No firmware was flashed, host settings changed, or device
registers accessed directly.

The board reports NVIDIA DGX Spark revision A.7, BIOS `5.36_0ACUM027`, and SoC
firmware **2.155.11**. Emulation uses the separately downloaded **2.158.15**
capsule. These findings corroborate interfaces, but are not a capture of the
exact emulated firmware version or its preceding boot-ROM handoff.

| Interface | Evidence from ACPI and kernel resource inventory | QEMU coverage |
| --- | --- | --- |
| GIC distributor | MADT base `0x06800000`, revision 4 | Existing GICv3 compatibility model |
| GIC redistributors | Base `0x06880000`, physical length `0x00500000` | One CPU / one `0x20000` redistributor |
| GIC ITS | `0x06840000` | Not modeled — and nothing on the modelled path touches it: a boot to setup produces no unmapped-access entry in the QEMU log |
| Console UART | SPCR 16550, base `0x16a00000`, 32-bit accesses, GSI 878 | 16550, register shift 2, interrupt input 846; synthetic IRQ test passes |
| External interrupts | `NVDA9221`, bank bases `0x12410000`, `0x12e00000`, `0x12800000`, `0x1c54a000`, parent GSI 563 | Configuration and software IRQs; external pins and debounce timing absent |
| ROT0 | `NVDA8600`, `0x18010000`, length `0x1000` | Unimplemented by default; optional transport error replies |
| ROT1 | `NVDA8601`, `0x18020000`, length `0x1000` | Same optional failure scenario |
| SBSA Generic Watchdog | GTDT refresh frame `0x1c041000`, control frame `0x1c042000` | QEMU `sbsa_gwdt` at 1 GHz with WS0 interrupt wired and stage-2 system reset |
| PCI ECAM | Segments 0–14 at `0xf300000000` plus `0x10000000` per segment; segment 15 at `0x29000000` | Minimal type-1 roots for segments 0–14; segment 0 optionally connects the lab RTL8168 NIC; no GPU |

ACPI identifies the eleven existing GPIO/pad banks as part of `NVDA9221` too.
Its resource list and device properties associate the four EINT banks with
the same controller. NVIDIA's public
[MT8901 pinctrl driver](https://github.com/NVIDIA/NV-Kernels/blob/0697f87f98083d0af96676725709e760543b7a07/drivers/pinctrl/mediatek/pinctrl-mt8901.c)
matches this ACPI HID. The public
[EINT implementation](https://github.com/NVIDIA/NV-Kernels/blob/0697f87f98083d0af96676725709e760543b7a07/drivers/pinctrl/mediatek/mtk-eint.c)
documents status/acknowledgement, mask, sensitivity, software-pending, polarity,
domain-enable, and debounce configuration offsets used by the model. No
register initialization dump was taken from the physical controller.

The ROT identity resolves the earlier `SaveApplLog` fault's device ownership;
it does not document the register protocol or authenticate the optional
error-reply model. That transport comes from local analysis of the unchanged
UEFI caller and remains unverified against live hardware transactions.

## Flash: the measured image layout

The board's part is a 64 MiB SPI NOR (the firmware's part table lists
`MX25U51255F`, `W25R512NW` and `MX77U51250F`). The released images are 32 MiB
firmware volumes: `_FVH` sits at `0x28` in each, and `uefi.fd` is the **code**
volume, not the variable store — parsing it yields zero variable records. The
device therefore carries two consecutive 32 MiB volumes:

| Offset | Size | Contents |
| --- | --- | --- |
| `0x0000000` | 32 MiB | Variable store volume (the bytes a boot rewrites) |
| `0x2000000` | 32 MiB | Firmware code volume, byte-identical to `uefi.fd` |

Booting a composed image of that shape rewrites only the store half (17 bytes
on a first boot) and leaves the code half byte-identical, which is what makes a
write to the code region a meaningful experiment. Fill the store with
variables and the firmware switches bank as it does on hardware: both bank
tails end up present at `0x3fe80` and `0x7fe80` inside the store volume.

## Flash: no DXE block device in this firmware build

A lab application calling `LocateHandleBuffer(ByProtocol, BlockIo)` returns
`EFI_NOT_FOUND` and no device list: the firmware publishes no `BlockIo` handle
at all under emulation. The SPI NOR host driver (`MediaTekSpiNorHost`) is loaded
— its PE sits at `0x848bf000` with a `0x18b10`-byte `.text` — but it never
touches a controller: a run with `-d guest_errors,unimp` logs no unmapped
access, and no module in the DXE image publishes the `MtkSpi0..7` handles its
name table references. Its controller base is not a constant in its own image
either (no non-DRAM address in `.text`, no page-aligned base in
`0x10000000..0x1fffffff` in `.data` apart from the UART), so the resources come
from a platform layer that this build does not appear to bind. Flash access in
the emulator therefore goes through the MM flash service, not a block device.

To repeat inventory collection on an authorized host:

```sh
python3 tools/collect_hardware.py USER@HOST
```

The collector requires remote Python 3 and passwordless `sudo` for protected
inventory reads. It creates no remote files and runs no package installation,
firmware update, reboot, or hardware-control command. Its local archive is
extracted with member-type, size, and path checks. Review any derived report
for identifiers and redistribution restrictions before publishing it.

## How the container states its own layout

Two declarations inside the artifact make the base and every extent checkable without trusting the
component map:

- **The base is declared.** The slot image carries `BRLYT\x00\x00\x00` at offset 0x200 and a 32-bit
  pointer at 0x210; in all three capsules and both slots of the part image that pointer is exactly the
  position of the `AAAA` header (0x203000). `header_position()` prefers the declaration and falls back
  to the magic scan only when there is none, and a declaration pointing at no header is an error.
- **Every component states its own length.** 26 of the 27 non-blank components are CBOR-wrapped
  (`0x59`); after the CBOR header comes a descriptor table (version 1, count, `deadbeefdeadbeef`,
  then `count` 0x20-byte sub-records). The last sub-record's sub-image offset, header length (always
  `0xd0`) and body length add up with the CBOR header to the component's size, to the byte, for all
  26. `BCT` is the exception — it is the patch, not a container.
- **The payloads name themselves.** At the body offset the component states, `STMM`, `FLASH` and `ESPI`
  begin with `SPKG` version 2 and carry an **Arm FF-A partition manifest**: `StandaloneMm Firmware`,
  `Flash Service` (partition 0x8002; owns the QSPI register window, SMMU stream id 180) and `eSPI
  Service` (partition 0x8003, stream id 181). `UEFI` begins with the MediaTek FILEINFO descriptor;
  `BL31`, `BL32`, `TPM`, `SCP`, `GPU` and the power firmware are opaque at their body — consistent with
  signed or encrypted content. Only `STMM` carries an `_FVH` firmware volume.

  **The three SPKG images are plaintext AArch64, not encrypted**: `FLASH` (172 728 bytes, entropy 5.55) and
  `ESPI` (266 488 bytes, 5.62) both begin with `b +0x40`, and `STMM` (446 464 bytes, 3.80) begins with a
  branch and carries UEFI variable names. The image begins 0x2000 into the package, not at the body
  offset — checking the body alone is what made an earlier revision call these "opaque".

  An earlier revision of this page said those device trees were empty after the root node. That was
  wrong: device-tree names and values are padded to four bytes, and the parser stepped one byte past the
  empty root name. `research/spkg-manifest.json` is the corrected record.
- Together with the UEFI decode (the FILEINFO stream ends within 2–4 bytes of the component's end and
  produces the pinned image), that pins both ends of every range in this capsule, not just one
  component. `tests/test_container.py` asserts all three.

## The flash transport, end to end (2026-11-10)

Read from the plaintext `FLASH` image and reproduced in the model:

| question | answer | evidence |
| --- | --- | --- |
| how wide is a read address? | **32-bit**; `> 0xffffffff` refused | handler `0x1eacc`, image `0x1eb84` |
| where does the data come from? | `memcpy` out of the window at `0x50000000 + address` | callee `0x2392c`, image `0x23a18` |
| what bounds it? | `address + count` must not carry out of 32 bits, and a runtime size field | image `0x2397c`, `0x239e4` |
| does the 16 MiB ceiling apply? | **no** — that is the program/erase helper's 24-bit address | `FLASH-SERVICE-WRITE-PRIMITIVE.md` vs the above |
| when is a normal-world caller refused? | once the lockout byte is set, by anyone not `0x8004` | gate `0x1e3a4`, arming `0x1df54` |
| who sets it, and when? | STMM, at `ReadyToBoot`, with command 8 | emulator trace `FLASH cmd=08` |

The window is therefore real, and the only caller shape that fits it is one that runs before the arming
request — a driver dispatched during DXE. This firmware publishes no file system to a boot option (32
device paths, none with a file-system protocol), so a `Driver####` entry cannot name a file here, and a
PXE image's own `FilePath` is empty. The remaining shapes are a firmware-volume change or code inside a
secure partition; both are outside what this repository does.

## The eSPI Service is also where the power limits live

The `ESPI` component (plaintext, image sha256 `67b9650b…`, 266 488 bytes) carries
`espi_svc/esvc_ec_power_limit.c`, the string `write_power_limit_handler`, and a `Set Trip point value`
path, and it addresses 21 SPBM registers. One block of six —
`0x06000714`, `…718`, `…71c`, `…720`, `…724`, `…728` — belongs to the power-limit handler: `0x06000718`
is read as a 4-byte type at image `0x369ec`, and the rest follow at `0x36ab0`-`0x36bc4`.

That is the same `0x0600xxxx` family as the fan commands (`0x06000704/706` read, `0x06000790…793` write),
and the fan path is measured working on an `ACUM028` unit. What is **not** established is whether the
gateway accepts an id outside the documented fan family: the fan ids do not appear in this image at all,
because the translation from `SMC 0xC400008D` happens in EL3, and `BL31` is encrypted.

`tests/fixtures/pl_probe.c` is a read-only probe for those six registers — it builds no write opcode and
refuses any id outside the six. Under the profile it reports `REFUSED` for each, which is what the model
says too, because the model is built from the only documentation there is. Whether a unit answers
differently is an open question, not a finding.

## Known model limitation: partition discovery

A live unit enumerates twenty FF-A partitions (`research/ffa-partitions-live.json`: the flash service at
`0x8002` with two UUIDs, the fan and RTC services at `0x8003`, STMM at `0x8004`, and fifteen others).
The emulator's monitor answers `PARTITION_INFO_GET` with **one** — STMM — because that is the only
partition its modelled boot talks to. Sending the measured list would be more faithful, but the
descriptor encoding would have to be verified against a guest that parses it first, and emitting an
unverified structure has crashed this firmware before. The limitation is written in the handler.

## Who actually reads the component map

The capsule's table is read by the platform's own boot stage, not by the firmware that ends up
running. What that means for this repository:

- **The UEFI image carries none of the container's own strings.** `firmware/prepared/*/uefi.fd` (33.5 MB
  decompressed) has zero `SF_BOOT`, `MSS1` and `socfw` occurrences, and 14 `SPI`. That is a search for
  the byte strings a parser of this format would normally carry, not a proof that no code path here
  touches flash: a parser could recognise the format structurally and never name it.
- **`BL2` is not a mapped component.** The map names `BL31`, `BL32`, `UEFI`, `TPM`, `ESPI`, `STMM`,
  `SCP` and the power firmware, but no `BL2`. The stage that reads a boot-partition table has to run
  before the partitions it reads, so it is not in the map — it is the BootROM/BL2 stage, which the
  capsule does not carry. (`[INFERENCE]` on the identity: the evidence is its absence from the map
  plus the fact that the table's own base is where the header sits.)
- **The offsets are relative to the table's own position.** Slot layout: `[0x0 .. 0x203000) reserved`,
  the `AAAA` header and its `BBBB` entries at `0x203000`, first partition at `0x204000`. Entries are
  relative to `0x203000`, which is why `BCT` recorded at `0x1992000` is at slot `0x1b95000`.
- **A whole-part image obeys the same rule, per slot.** In the recovery media's 64 MiB part image,
  slot 0 (`0x100000`..`0x2000000`) and slot 1 (`0x2000000`..) each put the header at slot offset
  `0x203000` and the `BCT` at `0x1b95000`. A slot must be delimited before its header is unique: the
  unbounded slot-0 slice sees two headers and the tool refuses it rather than picking one.
- **The recovery media ships the capsule verbatim** (`usb/fw/socfw.cap`) next to `set_cap_flag.bin`
  (8 bytes) and boots the FastOS installer. That installer *was* examined, because the flasher is the
  one place vendor code would state the offsets: `fastos.partaa` and `fastos.partab` are one XZ stream
  split at 4 GiB (concatenate before decompressing — `partaa` alone reports "compressed data is
  corrupt"), and the stream is a 37 284 315 136-byte ext4 rootfs. In that whole rootfs there is **no
  `SF_BOOT` string at all**, and the eight `MSS1` hits resolve to unrelated binaries (the GStreamer
  RIFF parser, an ffmpeg build, QtWebEngine). The EC and USB-PD capsules carry no `SF_BOOT` either.
  So none of the artifacts in this tree carries the strings a container parser would need, and
  nothing here claims what the installer does at run time.
- **What is verified:** for all three capsules (2.155.11, 2.158.15, Dell 4.1.0) the `UEFI` component
  taken at `base + offset` contains exactly one FILEINFO descriptor that decodes to the 33 554 432-byte
  firmware image this repository boots, matching the pinned hash. That is the check
  `tests/test_container.py` runs, and it is what proves the base rather than assuming it.

## How Linux actually reaches these partitions (from the kernel we hold)

The recovery media carries an uncompressed ARM64 kernel (`vmlinuz.Image`, 7.0.0-1018-nvidia). Its symbols
and strings answer the routing question without a unit:

| found | meaning |
| --- | --- |
| `acpi_ffh_address_space_handler`, `acpi_ffh_address_space_setup` | `CONFIG_ACPI_FFH`: FFixedHW opregion accesses are serviced from the OS |
| `ffa_transport_init`, `ffa_msg_send_direct_req` (via `ffa_*` set), `ffa_bus_type`, `ffa_device_match_uuid` | the Arm FF-A bus: partitions are enumerated and matched **by UUID** |
| `arm-ffa-%d`, `ARM-FFA-SRI`, `ARM-FFA-NPI`, `MSFT000C` | the ACPI bindings: the FF-A device, its interfaces, and the `\_SB.FFA0` mailbox device |

So the non-secure route to a secure partition has three doors, all vendor-declared: the AML FFH mailbox
(`\_SB.FFA0.FFAC`), UUID-matched FF-A devices on the `arm_ffa` bus, and the SMC the fan methods use. Reach
is not authorisation — normal-world flash requests were recorded being refused with denial `0x0A` — and the
mailbox's availability method `AVAL` returns a constant `1`, i.e. the DSDT asserts the mailbox exists
rather than probing it.
