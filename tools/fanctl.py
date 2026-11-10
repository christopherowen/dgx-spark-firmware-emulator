#!/usr/bin/env python3
"""Set the DGX Spark's fans from the firmware's own ACPI fan device.

The board's DSDT declares `FAN0` and `FAN1` as `PNP0C0B` fans with a `_FSL` method that takes a
level 0..100; the kernel exposes exactly that as a thermal cooling device, so a level reached
through `/sys/class/thermal` is the firmware's own path — no kernel module, no pinned EC revisions,
no hard-coded rpm. Whatever the EC or the DSDT does in a later firmware version, this follows it.

What it deliberately does *not* do: invent a fan interface when the firmware does not publish one.
If no fan cooling device is present the tool says so and changes nothing, rather than falling back
to an SMC write whose contract has not been established on that machine.

The firmware answers `_FIF` with fine-grained control at step 1, which is what makes the kernel's
cooling-device state *equal* to the `_FSL` level: 0..100, state for state. This route works on a unit
(measured on sp8: `_FST` returns live per-fan RPM with `CONFIG_ACPI_FFH=y`).

Read the level as a *pin*, not a curve: `_FSL` writes the same value into the EC's upper and lower
clamps, so a level holds the fans at one speed and stops the firmware's own curve, with a ceiling of
4350 rpm against fan 0's 9000 — and no ACPI path puts the clamps back to automatic. A reboot is the
way back.

usage:
    fanctl.py status
    fanctl.py set 60          # level 0..100, as _FSL takes it
    fanctl.py min             # level 0: the slowest the firmware drives the fans, 975 rpm
    fanctl.py --root /some/sys status     # a test tree
"""

import argparse
from pathlib import Path
import sys

THERMAL = Path("/sys/class/thermal")
# _FSL takes 0..100; the kernel maps it onto the cooling device's states, so the tool reads
# `max_state` rather than assuming a count.
FAN_HINTS = ("Fan", "FAN", "fan")


def fan_devices(root=THERMAL):
    """Every thermal cooling device the firmware's ACPI fan devices produced."""
    devices = []
    base = Path(root)
    if not base.is_dir():
        return devices
    for entry in sorted(base.glob("cooling_device*")):
        try:
            kind = (entry / "type").read_text().strip()
            maximum = int((entry / "max_state").read_text().strip())
            current = int((entry / "cur_state").read_text().strip())
        except (OSError, ValueError):
            continue
        if not any(hint in kind for hint in FAN_HINTS):
            continue
        devices.append({"path": entry, "type": kind, "max_state": maximum, "cur_state": current})
    return devices


def level_to_state(level, maximum):
    """The cooling-device state for a fan level.

    This firmware's `_FIF` reports fine-grained control with a step of 1, so the kernel's state is
    the `_FSL` level: 0..100, state for state. The tool still reads `max_state` and scales, so a
    firmware that steps in tens (max_state 10) or publishes an `_FPS` table keeps working — and a
    machine whose maximum is not 100 is reported, not assumed.
    """
    if maximum <= 0:
        return 0
    level = max(0, min(100, level))
    return min(maximum, round(level * maximum / 100))


def status(devices):
    if not devices:
        print("no ACPI fan cooling device found; the firmware does not publish the _FSL interface "
              "on this machine, and nothing was changed")
        print("the fan service itself may still be present: kernel/spark_fan.c issues the SMC directly "
              "and registers its own cooling device, which is the route on firmware without FAN0")
        return 1
    for device in devices:
        print(f"{device['path'].name}: type={device['type']} cur_state={device['cur_state']} "
              f"max_state={device['max_state']}")
    return 0


def set_level(devices, level, dry_run=False):
    """Write the level and check that the firmware kept it.

    The write goes through the kernel's ACPI fan driver into the DSDT's `_FSL`, which writes an
    `FFixedHW` operation region. On this firmware that region has no FFH `_DSM` descriptor, so the
    ACPI interpreter refuses it and the level does not stick — measured in the emulator:
    `ACPI Error: UNKNOWN_STATUS_CODE, During region initialization: [FunctionalFixedHW]`, then
    `Aborting method \_SB.FAN0.WRB`. Reporting the write as done would be a lie, so the level is
    read back and a device that did not take it is reported as refused.
    """
    accepted, refused = [], []
    for device in devices:
        state = level_to_state(level, device["max_state"])
        note = "" if device["max_state"] == 100 else " (this device does not step in 1s)"
        if dry_run:
            print(f"{device['path'].name}: level {level} -> state {state} of {device['max_state']}"
                  f"{note} [dry run]")
            accepted.append(device)
            continue
        try:
            (device["path"] / "cur_state").write_text(f"{state}\n")
        except OSError as error:
            print(f"{device['path'].name}: level {level} -> state {state} REFUSED by the kernel "
                  f"({error.strerror}): the firmware's _FSL did not take it")
            refused.append(device)
            continue
        readback = int((device["path"] / "cur_state").read_text().strip() or -1)
        if readback == state:
            print(f"{device['path'].name}: level {level} -> state {state} of {device['max_state']}"
                  f"{note}")
            accepted.append(device)
        else:
            print(f"{device['path'].name}: level {level} -> state {state} REFUSED, the device "
                  f"reports {readback}: the firmware's _FSL did not take it (the DSDT's FFixedHW "
                  f"region has no FFH descriptor, so the ACPI interpreter refuses the write)")
            refused.append(device)
    return 0 if accepted and not refused else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=THERMAL,
                        help=f"thermal class root (default {THERMAL})")
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="show the fan devices and their states")
    setter = sub.add_parser("set", help="set a fan level 0..100 through _FSL")
    setter.add_argument("level", type=int)
    sub.add_parser("min", help="level 0: the slowest the firmware drives the fans (975 rpm here)")
    args = parser.parse_args()
    devices = fan_devices(args.root)
    if args.command == "status":
        return status(devices)
    if not devices:
        return status(devices)
    if args.command == "min":
        return set_level(devices, 0, args.dry_run)
    if not 0 <= args.level <= 100:
        parser.error("level must be between 0 and 100, as _FSL takes it")
    return set_level(devices, args.level, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
