# Roadmap

The end goal is reproducible DGX Spark firmware execution. Completion requires
a real Spark component reaching a defined checkpoint; generic UEFI boot is
only preparation.

## 0. Establish the lab

- [x] Record primary sources and separate evidence from unknowns.
- [x] Inventory local firmware artifacts by hash and recognized outer header.
- [x] Boot generic EDK II to the UEFI shell under QEMU TCG locally.
- [ ] Validate an appropriate Renode core against the selected component.

The local baseline used QEMU 11.1.2 with the Homebrew-provided
`edk2-aarch64-code.fd`, reporting `edk2-stable202408-prebuilt.qemu.org`.
The committed smoke test reproduces the check; its firmware and raw logs are
not committed.

## 1. Choose and characterize one Spark firmware component

- [x] Obtain an exact vendor SoC update locally (LVFS release 152274).
- [x] Record the Founders Edition component, revision, upstream URL, and SHA-256.
- [x] Identify containers, compression, authentication wrapper, and UEFI executable boundaries.
- [x] Establish the UEFI test-entry ISA/load/entry/EL2 contract; physical boot-ROM handoff remains unverified.
- [x] Collect a read-only hardware inventory: ACPI tables, resource
      addresses, IRQ assignments, firmware component GUIDs, and boot log.

The captured machine runs SoC 2.155.11; the pinned emulator image is 2.158.15.
Cross-check address facts against both; this is not an exact-version capture.

Acceptance: an evidence-backed load contract for one selected component.
An update capsule alone is not sufficient; its payload may require extraction,
relocation, dependencies, and a boot context.

## 2. First Spark instruction and checkpoint

- [x] Implement the minimal test configuration and load the unchanged selected executable.
- [x] Verify SEC/DXE entry with breakpoints and single-steps, and trace unknown MMIO.
- [x] Add only the memory, UART, timers, interrupt controller, and services
      required to reach the selected checkpoint.
- [x] Save a reproducible command, input hashes, emulator revision, local trace,
      expected checkpoint, and list of approximated services.

Acceptance: repeatable execution of the selected real component to a known
checkpoint, with unsupported behavior visible rather than silently ignored.

Implemented: real SEC/DXE, real Standalone MM variable services, all required
architectural protocols and interactive AMI setup. Keyboard navigation and a
second boot with persistent NOR state pass. See [boot status](boot-status.md).

## 3. MT8901-related interfaces and controller execution

- [ ] Verify the reference SSPM/power_wrap interface against the Spark's ACPI.
- [ ] Model mailbox ownership, doorbells, responses, and interrupt delivery.
- [ ] Separate protocol stubs from actual remote firmware execution.
- [ ] Identify the remote core and image; choose QEMU or Renode from evidence.
- [x] Add independent subsystem regression scenarios for GPIO, UART IRQ,
      EINT software IRQs, and Root of Trust failure replies.

Acceptance: recorded request/response scenarios match observed hardware, and
any claim of controller firmware execution includes a real instruction trace.

## 4. Expand toward system firmware boot

- [x] Add the synthetic loader/monitor handoff and required MM, flash and RTC services.
- [x] Boot real Spark UEFI and MM to an interactive setup console.
- [x] Boot an original EFI application over native Spark IPv4 PXE; verify DHCP and exact TFTP transfer.
- [x] Document model gaps, including absent GPU/physical NIC firmware, warm reset and secure isolation.

Further work: boot an OS using modeled storage, implement warm reset and fuller
PCIe/USB behavior, and replace remaining loader/board approximations with
verified interfaces.

Full GB10 CPU microarchitecture fidelity, GPU computation, thermal/electrical
simulation, and hardware flashing are separate workstreams. They are not
implied by reaching a firmware checkpoint.
