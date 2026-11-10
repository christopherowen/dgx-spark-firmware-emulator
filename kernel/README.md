# The control module

`spark_fan.c` issues the SMCCC service the DSDT's `_FSL` issues, and exposes it as a cooling device.
It exists as a *testable, conservative alternative*, not because ACPI is broken: measured on a unit
(2026-11-09, read-only), the vendor kernel has `CONFIG_ACPI_FFH=y` and `\_SB.FAN0._FST` returns live
per-fan RPM, so ACPI fan control works there and this module is optional. Where ACPI is unavailable —
the recovery installer's kernel inside this repository's emulator is one such guest — this is a
working path. See `docs/fan-control-from-dsdt.md`.

What it does: registers one thermal cooling device, `spark-fan`, with `max_state` 100; a state maps
to `rpm = 975 + (4350 - 975) * state / 100` and is sent as the four sub-commands `0x06000790..793`
the EC's upper and lower clamps take, low byte then high byte; `get_cur_state` reads `0x06000704`
back. Every call checks
for the completion id `0xC400008E` and returns `-EIO` if the service stops answering, so a firmware
that changes its mind shows up as an error rather than a silent no-op.

What it deliberately does not do: pin an EC capability revision, hard-code a firmware version, or
invent a state table. `_FIF` reports fine-grained control with step 1, so 0..100 is the interface the
firmware publishes.

## Building it

The vendor kernel's headers are a DISTRO artefact, not something a kernel image carries. Two ways to
get them, and both end in the same command:

**On the unit** (nothing is written outside the module directory):

```sh
sudo apt-get install linux-headers-$(uname -r)        # if not already present
make -C /lib/modules/$(uname -r)/build M=$PWD modules
```

**From a unit's headers, read-only** — how the module in this repository was actually compiled
(kernel `6.17.0-1031-nvidia`):

```sh
ssh <unit> \
  'tar -C / -h -cf - usr/src/linux-headers-$(uname -r) lib/modules/$(uname -r)/Module.symvers' \
  | tar -C ~/spark-headers -xf -
python3 tools/build_module.py --headers ~/spark-headers/usr/src/linux-headers-$(...) \
  --vermagic "6.17.0-1031-nvidia SMP preempt mod_unload modversions aarch64"
# kernel/spark_fan.ko  176224 bytes
#   vermagic: 6.17.0-1031-nvidia SMP preempt mod_unload modversions aarch64
#   parm:     allow_pin:... (bool)
```

That builder exists because four things are missing from a header package on a foreign host, and each
one is explained where it happens: `scripts/basic/fixdep` and `scripts/mod/modpost` ship as aarch64
binaries (fixdep is rebuilt for the host; modpost cannot be, so modinfo is written by hand and the
object linked with `ld.lld -r`), the unit's gcc-only flags are stripped by a shim, and `ARCH=arm64`
has to be stated or kbuild looks for x86 asm headers.

Signing is whatever the unit requires (MOK enrollment if Secure Boot is on; no spaceport host trusts
the fleet key yet, so no module — ours or anyone's — has loaded on one). Then:

```sh
# one or the other, not both: the second insmod fails while the first is loaded
sudo insmod spark_fan.ko                    # inert: every level write is refused
sudo insmod spark_fan.ko allow_pin=1        # opt in, knowing a reboot undoes it
# (to switch: sudo rmmod spark_fan, then load it the other way)
ls /sys/class/thermal/                      # a cooling_device* with type "spark-fan"
echo 60 | sudo tee /sys/class/thermal/cooling_deviceN/cur_state
cat /sys/class/thermal/cooling_deviceN/cur_state   # 60 if the service kept it
```

## Honest status after the unit measurement

The vendor kernel on a unit drives `_FSL` itself (`CONFIG_ACPI_FFH=y`, `_FST` returning live per-fan
RPM), so this module is **optional**, not the working path. It builds — against headers copied read-only
from a unit, see *Building it* above, producing `kernel/spark_fan.ko` with that kernel's vermagic — but
it has never been loaded on a host, because no spaceport host trusts the fleet signing key yet — and
the artefact this repository builds is not meant for loading anyway: its modinfo is written by hand
(modpost ships as an aarch64 binary and cannot run here), so build on the unit for anything loadable.
What is verified: that the source compiles against the unit's real headers, the encoders against the
emulator's model, the rpm/level arithmetic, and the source-level guard that the answer is read from x4.

## What is verified here, and what is not

`tests/test_kernel_module_encoding.py` compiles the encoders for the host and checks their request
words against the emulator's model of the firmware (`monitor/fan.c`), which was derived from the DSDT
independently: the write words for levels 0, 50 and 100 are the ones the model claims and answers,
the two status commands are answered per fan, and both implementations compute the same rpm for
every level. `tests/test_fan_model.py` and `tests/test_fan_aml.py` cover the model side, including
executing the vendor's own `_FSL`/`_FST` under ACPICA.

Not verified here: that a unit's EC honours the service — that needs the unit. Nothing in this
repository has run on hardware, by instruction.
