# Measured Spark boot status

Verified locally on 2026-10-28 with QEMU 11.1.2 TCG and one AArch64 CPU:
**the real Spark UEFI boots through BDS to interactive AMI setup.** Keyboard
navigation from Main to Advanced works. A second cold boot reuses the saved
variable store and passes the same checks.

## Inputs and execution

Official LVFS release **152274**, SoC **2.158.15**, supplies both unchanged
vendor images. `tools/spark_image.py` pins and verifies the cabinet, capsule
and 32 MiB UEFI hashes. `tools/prepare_mm.py` extracts the SPKG v2 Standalone MM
package and verifies SHA-256
`591d5b33bad81bed6ec402dd2a5d20ed37bd3920d261420e0c4e3ba09d9431fa`.
No vendor images, captures or disassembly are published.

The original lab monitor starts at `0x80020000` in EL3. It boots the real MM
package at `0x93a40000` in S-EL1, receives its initialization-complete wait, and
starts UEFI at `0x80800000` in NS-EL2. It routes actual MM communications rather
than installing a substitute variable protocol. The real MM driver formats
and operates on the emulated NOR variable partition. A modeled PMIC RTC
service supports the real UEFI clock driver.

The synthetic loader memory tag at `0x80010000` has size `0x1868`, plus its
terminator, because SEC copies the complete tag into a GUID HOB. It reserves
live monitor, MM, communication/page-table and NOR backing ranges. This is an
inferred laboratory entry contract. No preceding boot ROM, vendor BL31 or
production partition manager is executed. Hash checks do not validate the
capsule PKCS#7 trust chain or establish authenticated boot.

## Verified results

| Check | Result |
| --- | --- |
| Unchanged SEC and DXE core | Executed; the separate stock-QEMU diagnostic also verifies exact entry breakpoints and single-steps |
| Real Standalone MM firmware | Initializes, waits and processes real UEFI variable requests |
| NOR-backed variable services | Read/program/erase operate on local persistent emulated flash |
| All 13 DXE architectural protocols | Present; complete ordered GUID table and terminator validated in paused guest memory |
| BDS and console initialization | Completes; serial checkpoints include `AD`, `A9`, `AB` |
| AMI setup | Interactive Main screen, real clock advancing |
| Keyboard input | Right-arrow opens Advanced and its settings list |
| Subsequent cold boot | Existing NOR state loaded; setup, navigation and all protocols verified again |
| GPU, NIC or operating system | Not part of this result |

The 13 protocols are Security, Cpu, Metronome, Timer, Bds, WatchdogTimer,
Runtime, Variable, VariableWrite, Capsule, MonotonicCounter, Reset and
RealTimeClock. A translated code trace or a printed checkpoint alone is not
accepted as a full-boot result.

## Reproduce

```sh
python3 tools/build_qemu.py
python3 tools/boot_spark.py --smoke-test
python3 tools/boot_spark.py --smoke-test
python3 tools/boot_spark.py
```

The smoke test sends Escape at the firmware's setup prompt, waits for the
actual setup title, sends a right-arrow and checks the Advanced settings.
It then stops the CPU, inspects the architectural-protocol table and saves
NOR state atomically. A firmware exception or missed boot deadline fails the
run. Interactive mode accepts serial keyboard input; Ctrl-C saves and exits.

Evidence stays under ignored `firmware/runs/<UTC timestamp>/`: `serial.log`,
`qemu.log`, the paused DXE snapshot, generated handoff, hashes and `run.json`.
The record links each run's input/output NOR hashes, allowing persistence to
be checked without publishing any generated variable-store contents. Failed
runs preserve the previous NOR file. Concurrent runs cannot share one store.

## Model boundaries

The [QEMU profile](../qemu/README.md) provides functional GIC/timers/UART and
QEMU xHCI cores, plus bounded configuration models for GPIO, EINT, USB PHY,
PCIe and board information. The original [monitor](../monitor/README.md)
implements only the observed FF-A compatibility subset and flash/clock peers.
Root of Trust requests receive explicit nonzero errors; no attestation or
security/logging service is reported as successful.

PCIe roots have no downstream devices. USB PHY register readback and clock
status approximate initialization, without electrical behavior. Loader-cached
board/version data and most fuse fields use synthetic defaults. Setup's
SMBIOS total-memory field currently shows 0 GB even though UEFI runs in QEMU
RAM; this display is not a physical memory inventory. Physical ACPI reports
GICv4 and 20 CPUs; the lab uses GICv3 and one CPU. The inventoried hardware ran
SoC 2.155.11, a different revision from the emulated update.

There is no full GB10/MT8901 implementation, GPU, physical-device passthrough,
secure stage-2 isolation, production secure boot, firmware flashing, warm
reset or migration support. Use a fresh QEMU process per cold boot. Reaching
setup establishes UEFI boot, not execution of every component in the SoC image.

The older `run_spark.py` diagnostic deliberately omits the monitor/MM package.
It remains useful for SEC/DXE entry checks and explicit failure scenarios;
its continuations are not the full-boot runner.
