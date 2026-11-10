# Fan control, read from the firmware's own DSDT (2026-11-09)

**Status:** SEEN. Decompiled from the DSDT the vendor firmware publishes, captured in the emulator.

## How to get to it

`tests/fixtures/nvram_census.c` runs under the profile over the PXE lane, prints every ACPI table
the firmware exposes (signatures, lengths, guest addresses) and — because the DSDT is not in the
XSDT — follows the FACP's `X_DSDT` pointer and prints that too. `tools/boot_spark.py` dumps each
named table from guest memory while the guest is stopped, into the run directory.

On 2.158.15 that is 17 tables plus a 71,058-byte DSDT. `iasl -d` turns the DSDT into ASL; the fan
interface is in it.

## Which firmware has this device — check before you look for it

Measured read-only across four units on 2026-11-09:

| firmware | DSDT size | `FAN0` / `FAN1` present |
| --- | --- | --- |
| BIOS `5.36_0ACUM027` (three units) | 70 143 bytes | **no** |
| BIOS `5.36_0ACUM028` (one unit) | 71 058 bytes | **yes** |

So the ACPI fan path in this page exists only on `ACUM028` and later. On `ACUM027` there is no
`FAN0`, no `_FSL`, no EC device and no `/sys/class/thermal` device to write: the fans are still driven,
but nothing exposes them through ACPI, and none of the commands below apply. This is also why the
emulator models a *newer* firmware than most units run — it was captured on `ACUM028`.

**Measured on an `ACUM027` unit (read-only).** Its DSDT (`1009b952…`, 70 143 bytes) declares no
`PNP0C0B` fan device, no `PNP0C09` EC device and no `_FSL` anywhere; it does declare seven thermal
zones, each reading a memory-mapped register (`\_TZ.TSOC._TMP` returns `RREG (0x1C238818)`, and so on),
exactly as `ACUM028` does. On that unit the kernel exposes seven `hwmon` devices and **not one `fan*` or
`pwm*` attribute**: five `mlx5`, one `acpitz`, one `mt7925_phy0`. Temperatures are readable; the fans are
not addressable through anything the firmware declares.

**That does not make `kernel/spark_fan.c` useless there.** The module does not use the ACPI fan device at
all: it issues the same SMCCC call the AML does and registers its own cooling device, so the ACPI table is
only how we learned the register numbers. On a unit whose firmware still answers `0xC400008D`, the module
controls the fans whether or not the DSDT declares `FAN0`. Whether `ACUM027` answers that call is
**untested** — its DSDT never names the registers, and the translation lives in encrypted EL3 code. The
honest statements are: on `ACUM028` both routes work; on `ACUM027` the ACPI route is absent and the module
is the only software route, unproven.

**The service itself is measurably present there.** A live `ACUM027` unit enumerates FF-A partition
`0x8003` with UUID `884a63a0-3285-4120-83aa-eec008a0a546` — the two 64-bit constants the SMC passes in
`x2`/`x3`, read little-endian. What is untested is whether it answers the register reads. On that unit
Secure Boot is on, the kernel has `CONFIG_MODULE_SIG=y` without `SIG_FORCE`, module headers are present,
and the running kernel already carries `TAINT_UNSIGNED_MODULE`; so loading this module there is plausible
without MOK, but the cleanest first test is a `READ_ONLY` build of `tests/fixtures/fan_probe.c`, which can
only issue the two documented reads.

## What the DSDT says

```
Device (FAN0)
    Name (_HID, EisaId ("PNP0C0B"))     // Fan (Thermal Solution)
    Name (FMIN, 0x03CF)                 // 975 rpm
    Name (FMAX, 0x10FE)                 // 4350 rpm
    Method (_FSL, 1) { WFCF (Arg0) }    // _FSL: Fan Set Level, 0..100

    Method (WFCF, 1) {                  // level -> rpm -> SMC
        RPM = FMIN + ((FMAX - FMIN) * Arg0) / 0x64
        WRB (0x06000790, low byte)
        WRB (0x06000791, high byte)
        WRB (0x06000792, low byte)       // the *lower* clamp, same value (see the unit measurement)
        WRB (0x06000793, high byte)
    }

    Method (WRB, 2) {                   // a 0x90-byte SMC request
        XX0 = 0xC400008D                // SMCCC function id
        XX1 = 0x8003
        XX2/XX3 = 2A0634A8-4185-...     // the vendor's GUID
        XX4 = 0x09 | (Arg0 << 8) | (Arg1 << 0x28)
        RGS = SMCF = RGS                // OperationRegion SMCR, FFixedHW
    }

Device (FAN1) { _FSL (Arg0) { ^^FAN0.WFCF (Arg0) } }   // one write sets both
```

The disassembled DSDT says more than that summary did, and two of the details change how a control
path has to behave:

```
FAN0._FIF -> Package { 0, 1, 1, 1 }        // Revision 0, fine-grained control, step 1
FAN1._FIF -> Package { 0, 1, 1, 1 }        // same
FAN0._FST -> { Revision, FLVL, rpm }       // status read 0x06000704
FAN1._FST -> { Revision, FLVL, rpm }       // status read 0x06000706   <-- a different command
```

- **Fine-grained control with step 1** means the kernel's cooling-device state *is* the `_FSL`
  level: `max_state` 100, state 60 = level 60, and `_FSL(0)` is 975 rpm. There is no automatic
  level in this interface, so nothing may present "auto" as a state.
- **Each fan has its own status command**: `0x06000704` for `FAN0`, `0x06000706` for `FAN1`. One
  read does not answer for both, and a model that answers only the first reports fan 0's speed for
  fan 1.

So the fan is controllable from software through **`_FSL` (level 0..100)** on `FAN0`, implemented
as an SMC call with function id `0xC400008D` and sub-commands `0x06000790..0x06000793` carrying the
RPM as two little-endian bytes, written into both of the EC's clamps (see the unit measurement below:
the pair is upper and lower clamp, not two fans).

## What that means for the two goals

- **Fan control**: the interface is ACPI `_FSL`, and behind it one SMC function. Both a kernel
  (ACPI fan/thermal path) and an EFI application with an AML evaluator can reach it; a payload that
  issues the SMC directly needs only the function id and the sub-commands above.
- **Memory settings**: no memory device (`MR*`, `DIMM`, `SPBM`) appears in the DSDT, so they are not
  an ACPI interface. They are either NVRAM-backed (the `Setup` blob, which the census reads) or
  applied before UEFI by the boot chain (BCT/DDR training). Decoding `Setup` through the HII forms is
  what separates those two.

## Reproduce

```sh
clang --target=aarch64-pc-windows-msvc -O2 -ffreestanding -nostdlib -c tests/fixtures/nvram_census.c -o /tmp/c.o
lld-link /machine:arm64 /subsystem:efi_application /entry:_start /nodefaultlib /out:/tmp/c.efi /tmp/c.o
python3 tools/boot_spark.py --pxe-test --board --flash-image firmware/state/board-2158.bin \
    --firmware-version 2.158.15 --pxe-image /tmp/c.efi --pxe-marker CENSUS-END --timeout 120
iasl -d firmware/runs/<id>/acpi-DSDT-*.bin
```

`iasl` is not installed on this host and needs no root: `apt-get download acpica-tools`,
`dpkg -x` it, and run the extracted `usr/bin/iasl`.

## The service, modelled

`monitor/fan.c` implements the service behind that SMC call, in its own translation unit so it can
be driven from a host test:

- **write**: `x4 = 0x09 | (command << 8) | (value << 40)`, `command` `0x06000790..0x06000793`, one
  byte per call. `790/791` carry the upper clamp's low and high byte and `792/793` the lower clamp's —
  measured on the unit at host `0x06000600`, EC `0x119000`. Both receive the same value, which pins
  the fans rather than setting a second one;
- **read**: `x4 = 0x0C | (0x06000704 << 8) | (2 << 40)` returns the rpm in `x4`;
- the reply is `x0 = 0xC400008E`, the endpoint echoed in `x1`, and the register file cleared to
  `x17` — the same shape the RTC service on the same function id uses;
- `fan_rpm_for_level()` reproduces `WFCF`'s arithmetic exactly: `975 + (4350 - 975) * level / 100`.

`tests/test_fan_model.py` compiles it for the host and drives those exact requests: the level
arithmetic, the four byte writes and the read-back for levels 0, 50 and 100, the echoed endpoint,
and the refusals (another GUID, an unknown command, another function id). It caught its own first
mistake, which is worth recording: the service clears 18 registers, so a five-register test frame was
a buffer overrun that crashed the interpreter at exit with `SIGSEGV` *after* printing `OK`.

What the model is not: the EC. The interface and its arithmetic come from the firmware's own DSDT;
no result about a real fan follows from the model, and the fan *effect* on hardware still needs a
unit.

## The tool, and why it survives a firmware change

`tools/fanctl.py` drives the fans through `/sys/class/thermal`: the kernel turns a `cur_state` write
into the ACPI device's `_FSL`, which is the firmware's own path. It never pins an rpm, a state count
or an EC revision — it reads `max_state` from the device and maps a level 0..100 onto it:

```sh
fanctl.py status          # the fan devices and their states
fanctl.py set 60          # level 60 through _FSL
fanctl.py min             # level 0: 975 rpm, the slowest the firmware drives them
```

That is the difference from the kernel-module approach on the same hardware: that driver pins the
EC capability revision and four exact rpm values, so a new EC firmware makes it refuse
(`-EBADMSG`) rather than adapt. This one asks the firmware what it has and uses it — and if the
firmware publishes no fan device at all, it says so and changes nothing instead of writing an SMC
whose contract is not established there.

`tests/test_fanctl.py` runs it against synthetic thermal trees: 100 states, 12 states, 5 states, a
machine with no fan, dry runs, and a level outside 0..100.

## Executed, not only read

`tests/fixtures/fan_aml_check.asl` is a small SSDT that calls the vendor's own `_FSL` and `_FST`.
Where ACPICA's `iasl` and `acpiexec` are installed and a DSDT has been captured by the census,
`tests/test_fan_aml.py` compiles it and evaluates:

```
Evaluating \FSET          # \_SB.FAN0._FSL (50)
Evaluation of \FSET returned ... [Integer] = 0000000000000000
Evaluating \FGET          # \_SB.FAN0._FST ()
  [Package] Contains 3 Elements:
    [Integer] = 0000000000000000    # Revision
    [Integer] = 0000000000000032    # Control = 50, the level just set
    [Integer] = 0000000000000000    # Speed
```

So the level the kernel writes is the level the firmware stores and reports — the `cur_state` the
thermal class shows. The rpm is not visible this way: the SMC sits behind an `FFixedHW` region and
ACPICA has no handler for it, so the writes are inert there. The emulator run is the only place the
words the AML builds reach something that answers them, which is why both exist.

## Measured on a unit: the ACPI path works, and the claim that it does not is withdrawn

Read-only measurements on a unit running the latest LVFS firmware (2026-11-09) settle this:

- the DSDT there is **71 058 bytes**, the same size this repository captured, and a unit on the older
  SoC firmware has no `FAN0`/`FAN1` at all — the devices arrived with the newer firmware;
- `_FIF`, `_FSL`, `_FST`, the 975–4350 formula and both status addresses match the real AML;
- **`_FST` returns live, separate RPM per fan (1978 and 2072)**, and the vendor kernel has
  `CONFIG_ACPI_FFH=y` with no ACPI errors at boot.

So ACPI fan control **works on a unit**. The `Aborting method \_SB.FAN0.WRB` recorded below came from
the kernel this emulator boots — the recovery installer's — which does not install an FFH handler for
the region. That is a fact about that kernel, not about the firmware, and the text below is kept as
that observation rather than as a claim about the board.

The measurement also changes how `_FSL` must be read: on the unit `0x06000790` and `0x06000792` are the
**upper and lower clamps** in EC memory (host `0x06000600` = EC `0x119000`), so writing a level pins
the fans at one speed, stops NVIDIA's curve from ramping, caps them at that rpm (at most 4350, against
fan 0's 9000) and **nothing in ACPI restores the clamps to `0xFFFF`**. A reboot is the only measured
way back — which is why `kernel/spark_fan.c` refuses to pin unless `spark_fan.allow_pin=1` is given.

## What the recovery kernel in the emulator does

Driving the fan from userspace in the emulator exposed something the disassembly could not: the
`FFixedHW` operation region that `_FSL` writes has **no FFH `_DSM` descriptor** in the DSDT, so the
kernel's ACPI interpreter refuses the region. A freestanding init that writes
`/sys/class/thermal/cooling_device0/cur_state` produces:

```
ACPI Error: UNKNOWN_STATUS_CODE, During region initialization: [FunctionalFixedHW]
ACPI Error: Aborting method \_SB.FAN0.WRB
```

The cooling device exists (`type=Fan`), the level does not stick. **This is the recovery installer's
kernel, not a unit's.** The measurement on sp8 shows the installed vendor kernel has
`CONFIG_ACPI_FFH=y` and drives `_FSL` happily: `_FST` returns live per-fan RPM. So the conclusion is
about *this* guest, not about the firmware, and the earlier claim that `_FSL` is unusable on Linux is
withdrawn — see the measurement section above.

What survives is the more useful point: the service is reachable directly, which is what the
`0xC400008D` model in this repository does, and what a path of our own would have to do if a guest
kernel ever lacked the FFH handler.

`tools/fanctl.py` therefore reads `cur_state` back after writing and reports a device that did not
take the level as refused, instead of printing a level it did not set.

## Which firmware, exactly

`research/firmware-catalog.json` records the LVFS catalog as read on 2026-11-09. Each DGX Spark
component publishes exactly one release, so "the latest" is a date and a hash rather than a choice:

| component | version | published | local artifact |
| --- | --- | --- | --- |
| DGX Spark SoC FW | `0x2009e0f` | 2026-09-28 | `spark-soc-2.158.15.cab` — byte-identical to the LVFS artifact (`f8959528…`) |
| DGX Spark EC FW | `0x3000d07` | 2026-09-28 | `spark-ec.cab` |
| DGX Spark USB-C PD FW | `0x519` | 2026-08-31 | `spark-usbpd.cab` |

The capsule this repository calls `4.1.0` is the **Dell "Pro Max with GB10 FCM1253"** release
(`com.dell.uefic8a8158c`, 4.1.0): same silicon family, different vendor packaging, and it carries no
`0x…` version string of its own. It is a second capsule to test against, not a newer DGX Spark
release.

The fan probe passes on both:

```
$ boot_spark.py --firmware-version 2.158.15 --pxe-image fan_probe.efi   # LVFS 0x2009e0f
F 0 975 / F 50 2662 / F 100 4350 / R0 4350 / R1 4350 / FAN-OK
$ boot_spark.py --firmware-version 4.1.0  --pxe-image fan_probe.efi   # Dell 4.1.0
F 0 975 / F 50 2662 / F 100 4350 / R0 4350 / R1 4350 / FAN-OK
```

`tests/test_firmware_catalog.py` keeps the record tied to the bytes: the local capsule's own version
string and hash must be the ones the catalog names.

## Deploying the module on a unit

Only needed on a firmware that declares no `FAN0` — the fleet's `ACUM027`. Nothing here is required on
`ACUM028`, where `/sys/class/thermal` already has the fan devices.

```sh
# build against the kernel the unit actually runs (vermagic must match)
python3 tools/build_module.py --headers /lib/modules/$(uname -r)/build \
        --vermagic "$(uname -r) SMP preempt mod_unload modversions aarch64"

# load it READ-ONLY first: with allow_pin unset the module refuses every write and only reads
sudo insmod kernel/spark_fan.ko
python3 tools/fanctl.py status      # enumerates every cooling device, including spark_fan
sudo rmmod spark_fan
```

`fanctl.py status` reads each device's `cur_state`, and for the module that read is the documented SMC
read — so a number in the `spark_fan` row means the unit's firmware answers the fan service, and pinning
is available with `allow_pin=1`. If it reads `-EIO` (or `insmod` reports `-ENOKEY`), the service refused or the module was
not accepted: record which, and stop.

**State, as measured on one `ACUM027` unit:** Secure Boot on, `CONFIG_MODULE_SIG=y` with `SIG_ALL=y` but
no `SIG_FORCE`, kernel headers present, and `TAINT_UNSIGNED_MODULE` already set — so an unsigned module
has loaded there at least once, but a refusal at load time is still possible and harmless.

**The pin is the part to think about, not the read.** With `allow_pin=1`, writing a level sets both clamp
registers and nothing in ACPI restores them: a reboot is the only measured way back, and the fans stay
capped at the level's rpm (4350 at level 100, against fan 0's 9000) even under load.
