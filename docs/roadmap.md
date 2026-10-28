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

- [ ] Obtain an exact vendor update/package or owned-device dump locally.
- [ ] Record board model, component, revision, source URL, and SHA-256.
- [ ] Identify containers, compression, signatures, and executable boundaries.
- [ ] Establish ISA, load segments, entry point, exception level, and reset state.
- [ ] Collect a matching read-only hardware inventory: ACPI tables, resource
      addresses, IRQ assignments, firmware component GUIDs, and boot log.

Acceptance: an evidence-backed load contract for one selected component.
An update capsule alone is not sufficient; its payload may require extraction,
relocation, dependencies, and a boot context.

## 2. First Spark instruction and checkpoint

- [ ] Implement the minimal machine and load the selected executable.
- [ ] Trace the first executed instructions and first unknown MMIO access.
- [ ] Add only the memory, UART, timers, interrupt controller, and services
      required to reach the selected checkpoint.
- [ ] Save a reproducible command, input hashes, emulator revision, trace,
      expected checkpoint, and list of approximated services.

Acceptance: repeatable execution of the selected real component to a known
checkpoint, with unsupported behavior visible rather than silently ignored.

## 3. MT8901-related interfaces and controller execution

- [ ] Verify the reference SSPM/power_wrap interface against the Spark's ACPI.
- [ ] Model mailbox ownership, doorbells, responses, and interrupt delivery.
- [ ] Separate protocol stubs from actual remote firmware execution.
- [ ] Identify the remote core and image; choose QEMU or Renode from evidence.
- [ ] Add independent subsystem regression scenarios.

Acceptance: recorded request/response scenarios match observed hardware, and
any claim of controller firmware execution includes a real instruction trace.

## 4. Expand toward system firmware boot

- [ ] Add boot-chain handoffs and required secure firmware services.
- [ ] Reach a Spark firmware console or UEFI milestone using real components.
- [ ] Document every remaining model gap, including GPU and NIC dependencies.

Full GB10 CPU microarchitecture fidelity, GPU computation, thermal/electrical
simulation, and hardware flashing are separate workstreams. They are not
implied by reaching a firmware checkpoint.
