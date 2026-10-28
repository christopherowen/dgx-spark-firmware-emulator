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
| GIC ITS | `0x06840000` | Not modeled at the physical address |
| Console UART | SPCR 16550, base `0x16a00000`, 32-bit accesses, GSI 878 | 16550, register shift 2, interrupt input 846; synthetic IRQ test passes |
| External interrupts | `NVDA9221`, bank bases `0x12410000`, `0x12e00000`, `0x12800000`, `0x1c54a000`, parent GSI 563 | Configuration and software IRQs; external pins and debounce timing absent |
| ROT0 | `NVDA8600`, `0x18010000`, length `0x1000` | Unimplemented by default; optional transport error replies |
| ROT1 | `NVDA8601`, `0x18020000`, length `0x1000` | Same optional failure scenario |
| PCI ECAM | Segments 0–14 at `0xf300000000` plus `0x10000000` per segment; segment 15 at `0x29000000` | Generic `virt` topology remains approximate |

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

To repeat inventory collection on an authorized host:

```sh
python3 tools/collect_hardware.py USER@HOST
```

The collector requires remote Python 3 and passwordless `sudo` for protected
inventory reads. It creates no remote files and runs no package installation,
firmware update, reboot, or hardware-control command. Its local archive is
extracted with member-type, size, and path checks. Review any derived report
for identifiers and redistribution restrictions before publishing it.
