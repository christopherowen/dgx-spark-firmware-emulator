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
