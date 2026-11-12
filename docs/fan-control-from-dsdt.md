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
- **a read answers zero once before it answers**: measured on a unit (2026-11-11), the *first* read
  after a gap returns `0` with the success completion code, and the *second* returns the rpm — for
  either status id. Two consecutive reads of `0x06000706` gave `0x0` then `0x10e0` (4320 rpm), with
  the cooling device reading level 99 in the same instant. This is what made an earlier version of
  `kernel/spark_fan.c` conclude that "`0x06000704` answers 0", and it is why the module now issues each
  status read twice and keeps the second answer. It does **not** show that the two ids are one fan:
  both fans are driven to the same rpm by `_FSL`, so equal readings are what two identically-driven
  fans look like;

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

## The hwmon interface, and why `pwm1` is not the tachometer

`kernel/spark_fan.c` registers a thermal cooling device *and* an hwmon device (`spark_fan`):

| attribute | meaning |
| --- | --- |
| `fan1_input`, `fan2_input` | rpm from the two status ids, each read twice because of the warm-up above |
| `pwm1` | manual: requested level; otherwise: reported current level, scaled 0..255 |
| `pwm1_enable` | `2` = the module stops writing (the default), `1` = manual; neither `2` nor module unload releases an existing EC clamp |

A write to `pwm1` while `pwm1_enable` is `2` is refused with `EPERM` rather than silently ignored, and
`pwm1` reports the setpoint in manual mode rather than the reported current level: after `echo 20`
the measured level was still 87 four seconds later. An immediate readback need not match the setpoint.

## What the level actually is, measured on a unit (2026-11-11)

Two corrections that change how the interface should be used, both from one supervised run on an
ACUM027 unit:

- **the `_FSL` level is a floor, not a fan setting.** The scale maps level 0..100 to `975 + 3375·level/100`
  rpm — 975 to **4350 rpm** — and the EC is free to run the fans above whatever floor it is given. Two
  different levels are therefore often the same setting to the EC, which is why an A/B on the level
  measured nothing at all. Anything that must be audible has to ask for an rpm.
- **the transport carries the rpm itself.** The write commands `0x06000790..0x06000793` take two
  little-endian bytes of rpm, so a floor above the `_FSL` scale can be requested directly: `13500` was
  written and the service then reported **8820/12420 rpm**, audibly louder than the other units beside it.
- **this is the interface the firmware itself uses, not one inferred from behaviour.** The newest DSDT
  we hold (`ACUM028`) implements `FAN0._FSL` as `WFCF` → `WRB` with `XX0 = 0xC400008D`, `XX1 = 0x8003`,
  and writes `0x06000790..793` with `RPM = FMIN + ((FMAX - FMIN) * level) / 0x64` where `FMIN = 0x3CF`
  (975) and `FMAX = 0x10FE` (4350) — the same function id, endpoint, commands and scale this module uses.
  The older units' ACPI publishes no fan device at all, yet the service answers there, so the interface
  outlived its own ACPI description rather than the other way round.

Under the same 8-worker load for the same duration, the hottest zone peaked at **61 °C** with firmware
control, **60 °C** at a 4350 rpm floor and **55 °C** at 12500 — one run per arm, so an observation and not
a benchmark. The cooling-rate arms of the same script measured nothing, because the machine never rose
above the cool-to target in two of the three arms.

Unloading the module or writing `pwm1_enable=2` stops module writes but leaves the EC's clamps
as last written; neither restores automatic control. **A reboot is the only measured restore**,
which is why the module refuses to write without `allow_pin=1`.

## The third firmware: 4.1.0 (banner `0ACUM10`), which declares no fan device

The emulator carries three firmware versions, and the third is a different lineage from
`0ACUM027`/`0ACUM028`: version `4.1.0`, whose banner reads `0ACUM10`. Two measurements decide what can be
claimed for it.

**Its DSDT does not describe a fan.** Extracted from the capsule by decompressing the `MSS1`/`FILEINFO`
LZMA stream, then reading the `DSDT` table at its own header:

| image | DSDT bytes | `FAN0` | `_FSL` | `_FST` | `WFCF` | `_TMP` |
| --- | --- | --- | --- | --- | --- | --- |
| 4.1.0 (`0ACUM10`) | 70 143 | 0 | 0 | 0 | 0 | 9 |
| 2.155.11 (`0ACUM027`) | 70 143 | 0 | 0 | 0 | 0 | 9 |
| 2.158.15 (`0ACUM028`) | 71 058 | 3 | 2 | 2 | 3 | 9 |

The first two tables are byte-identical (same sha256), so `0ACUM10` and `0ACUM027` share one ACPI view:
thermal zones but no fan device, therefore no `_FSL`, no `WFCF` and no rpm literals to write.

**What its boot calls are not.** Booting that image with the monitor's SMC log shows 2071 calls to
`SMC 0xC400008D` with `x1 = 0x8003` and a constant `x2 = 0x20418532a0634a88`. It is tempting to read that
as the fan service — an earlier version of this note did — and it is wrong:

- `0xC400008D` is `FFA_MSG_SEND_DIRECT_REQ2`, the FF-A direct-request call, not a fan id;
- `0x8003` is `SPARK_FIRMWARE_PARTITION_ID` in `soc/uefi/include/FirmwareInventoryLayout.h` of the
  recovered firmware source;
- the constant is the low half of service UUID `884a63a0-3285-4120-83aa-eec008a0a546`, the
  firmware-inventory service, whose command 12 reads the EC version at `0x06000760` and the PD versions at
  `0x06000770`/`0x06000774`.

So those calls are version reads by `FmpEndOfDxe` and `SmbiosType45Dxe`. Whether the ASUS image's fan path
is live remains open, and it is not answered at `0x8003`.

## The EC's own fan code is readable, and it explains the measurements

The EC capsule has been recovered to source and rebuilt byte-identically
(`christopherowen/spark-open-firmware`, `ec/ecfw/src/thermal/thermalmgmt.c`). Its constants:

| constant | value |
| --- | --- |
| running duty range | 14 – 100 percent |
| slew | rises at most 10 per pass, falls by one, settled within 2 percent |
| publish threshold | 5 percent change |
| CPU fan | 1260 – 9000 rpm |
| rear fan | 1890 – 13500 rpm |
| absent host clamp | `0xFFFF` |

`thermal_update_fan_rpm()` computes the published speed from the duty —
`rpm = (duty-14)/(100-14)*(max-min) + min` — and publishes 0 below duty 14, on a zero tachometer, or on a
wrapping estimate. The tachometer never supplies the published value.

The code and measurements establish separate facts; the read-timing explanation remains inference:

- a first read after a gap answers 0 and the next carries an rpm — **[INFERENCE]**: this may reflect the
  5 percent publish filter, but no poll or EC-pass timing trace connects the two observations;
- a host floor stays pinned until reboot — nothing in this code clears a clamp;
- a 13500 rpm request produced 8820/12420 — 13500 is `FAN_REAR_MAX_RPM`, and both numbers are estimates at
  the resulting duty;
- the ACPI `_FSL` scale (975…4350) is not the EC's scale (1260…9000, 1890…13500), so the two sides of the
  interface never agreed.

A published `_FST` value is therefore an estimate, not a measurement, and must not be presented as one.

