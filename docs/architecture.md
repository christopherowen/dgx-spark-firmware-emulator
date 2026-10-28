# Backend decision and evidence

Decision date: 2026-10-28. Working focus: the DGX Spark SoC boot / UEFI path.
This choice can change when a concrete firmware component is available.

## What is established

NVIDIA's [Spark software stack documentation][nvidia-stack] describes UEFI,
capsule-based firmware updates, LVFS delivery, and ConnectX-7 networking.
Its [release notes][nvidia-release] track SoC, UEFI, embedded controller, USB PD,
and TPM firmware separately. An OS recovery image, an update capsule, and a
component executable are different artifacts.

The [MT8901 pinctrl work][pinctrl] exposes ACPI GPIO and wake-event behavior.
The [SSPM / power_wrap work][sspm] exposes host-to-firmware power requests using
a vendor SCMI transport. The [audio work][audio] concerns N1x/MT8901 SoundWire,
ASoC, and DSP power management. These are useful implementation references;
N1x laptop behavior must be checked against the selected Spark board and
firmware revision before treating it as a Spark hardware fact.

The [pinned SSPM source][sspm-source] provides concrete relative interface facts:

| Interface fact | Value in the reference driver |
| --- | --- |
| ACPI match IDs | `MTKW8400`, `NVDA8400` |
| Mailbox count | 2 |
| Mailbox stride | `0x10000` |
| Input doorbell offset within mailbox | `0x1000` |
| Output acknowledge/clear offset | `0x1004` |
| Vendor TINYSYS SCMI protocol ID | `0x80` |
| Physical base and interrupts | Obtained from platform/ACPI resources |

These offsets are not a complete hardware specification. In particular,
the physical base, IRQ assignments, reset state, firmware ISA, and executable
entry point remain unverified for our target. A host-side driver describes
one side of a protocol, not the remote processor's implementation.

## QEMU versus Renode

| Need | QEMU | Renode |
| --- | --- | --- |
| AArch64 application processor and UEFI work | Preferred starting backend; existing Arm system and reference-board models | Arm64 cores exist; exact firmware CPU/system requirements still need validation |
| Custom registers and controller behavior | C device models and board integration | C# peripheral models, platform descriptions, tracing and test APIs |
| Isolated MT8901-related controller firmware | Evaluate once its ISA and load contract are known | Strong candidate if the actual core and required features are supported |
| Unmodified DGX Spark firmware today in this project | Not supported | Not supported |
| GB10 GPU execution / CUDA | No implementation here | No implementation here |

QEMU's [`virt` board][qemu-virt] deliberately describes generic virtual hardware.
Its [`sbsa-ref` board][qemu-sbsa] is intended for firmware development, but also
has its own hardware contract. Neither is a substitute for a GB10 machine.
The installed QEMU 11.1.2 machine list had no DGX Spark / GB10 / MT8901 target.

Renode's [platform catalog][renode-platforms] did not list those targets when
checked. Its [Arm64 implementation][renode-arm64] and
[peripheral modeling guide][renode-models] make it worth evaluating rather
than ruling out. A platform description alone cannot supply missing device
semantics, firmware services, or CPU architectural features.

## Implementation boundary

Build toward a custom Spark machine description and only the devices exercised
by a chosen firmware checkpoint. Capture the PC and address of unknown MMIO
accesses. Record every stub, its observed behavior, and the checkpoint it
enables. A constant-success mailbox stub is a protocol approximation, not
execution of the remote controller firmware.

Do not assume the main AArch64 processor's ISA applies to the EC, SSPM, DSP,
USB PD controller, NIC, or other firmware processors. CPU feature requirements,
exception level, secure monitor/FF-A services, reset vectors, RAM/flash layout,
interrupt wiring, and capsule payload boundaries must come from evidence.

The initial tools do not change a physical Spark. They inspect local files and
run an offline generic VM. No register writes, update operations, or automatic
hardware collection are included.

[nvidia-stack]: https://docs.nvidia.com/dgx/dgx-spark-porting-guide/latest/porting/software-requirements.html
[nvidia-release]: https://docs.nvidia.com/dgx/dgx-spark/release-notes.html
[pinctrl]: https://github.com/NVIDIA/NV-Kernels/pull/564
[sspm]: https://github.com/NVIDIA/NV-Kernels/pull/570
[audio]: https://github.com/NVIDIA/NV-Kernels/pull/566
[sspm-source]: https://github.com/NVIDIA/NV-Kernels/blob/0697f87f98083d0af96676725709e760543b7a07/drivers/soc/mediatek/sspm_ci/sspm_ci.c
[qemu-virt]: https://www.qemu.org/docs/master/system/arm/virt.html
[qemu-sbsa]: https://www.qemu.org/docs/master/system/arm/sbsa.html
[renode-platforms]: https://renode.readthedocs.io/en/latest/introduction/supported-boards.html
[renode-models]: https://renode.readthedocs.io/en/latest/advanced/writing-peripherals.html
[renode-arm64]: https://github.com/renode/renode-infrastructure/blob/57856323c908f892de757bdac84bd04e5be348c0/src/Emulator/Cores/Arm64/ARMv8A.cs
