#!/usr/bin/env python3
"""Report what a running unit exposes for fan control — read-only, no modules, no SMC.

Run it *on* the unit. It reads sysfs only: the FF-A partitions the firmware enumerates, the thermal
zones, the hwmon devices and the thermal cooling devices. From those it answers two questions that took
several sessions to answer by hand:

  * does the firmware carry the fan service? (FF-A partition 0x8003 with the UUID the DSDT's SMC carries)
  * does the installed ACPI table expose a fan device? (a cooling device or a hwmon pwm channel)

Both are observations; neither proves the service answers a read. For that, the read-only EFI probe
(`tests/fixtures/fan_probe.c`, built with `-DREAD_ONLY`) or `insmod spark_fan.ko` with `allow_pin` unset
is the next step, and both need the owner's go.

Exit status is 0 when the fan service is present, 1 when it is not, 2 when sysfs could not be read.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# The two 64-bit constants \_SB.FAN0.WRB puts in x2/x3 of SMC 0xC400008D are this UUID, little-endian.
FAN_UUID = "884a63a0-3285-4120-83aa-eec008a0a546"
FFA = Path("/sys/bus/arm_ffa/devices")
THERMAL = Path("/sys/class/thermal")
HWMON = Path("/sys/class/hwmon")


def read(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def partitions(root: Path = FFA) -> list[dict]:
    found = []
    if not root.is_dir():
        return found
    for entry in sorted(root.glob("arm-ffa-*")):
        driver = entry / "driver"
        found.append({
            "device": entry.name,
            "partition_id": read(entry / "partition_id"),
            "uuid": read(entry / "uuid"),
            "driver": driver.resolve().name if driver.is_symlink() else "",
        })
    return found


def thermal(root: Path = THERMAL) -> dict:
    zones, cooling = [], []
    if root.is_dir():
        for zone in sorted(root.glob("thermal_zone*")):
            zones.append({"name": zone.name, "type": read(zone / "type"), "temp": read(zone / "temp")})
        for device in sorted(root.glob("cooling_device*")):
            cooling.append({"name": device.name, "type": read(device / "type"),
                            "cur_state": read(device / "cur_state"),
                            "max_state": read(device / "max_state")})
    return {"zones": zones, "cooling": cooling}


def fans(root: Path = HWMON) -> list[dict]:
    found = []
    if not root.is_dir():
        return found
    for device in sorted(root.glob("hwmon*")):
        channels = sorted(path.name for path in device.iterdir()
                          if path.name.startswith(("fan", "pwm")) and path.name[3:4].isdigit())
        found.append({"name": read(device / "name"), "channels": channels,
                      "fans": [c for c in channels if c.startswith("fan")],
                      "pwms": [c for c in channels if c.startswith("pwm")]})
    return found


def module_state(root_modules: Path = Path("/sys/module"), cooling=()) -> dict:
    """Whether the readback module is loaded and what its cooling device reports."""
    loaded = (root_modules / "spark_fan").is_dir()
    devices = [device for device in cooling if device["type"].lower() == "spark_fan"]
    return {"loaded": loaded, "devices": devices}


def report(root_ffa: Path = FFA, root_thermal: Path = THERMAL, root_hwmon: Path = HWMON,
           root_modules: Path = Path("/sys/module")) -> dict:
    parts = partitions(root_ffa)
    fan_partitions = [part for part in parts if part["uuid"].lower() == FAN_UUID]
    temps = thermal(root_thermal)
    sensors = fans(root_hwmon)
    # A fan is a cooling device whose type names one, or a hwmon fan/pwm channel. PCIe link-speed
    # limiters and processor powerclamps are cooling devices too, and they are not fans — an earlier
    # version of this check reported six of them as "ACPI fan surface present" on firmware that has none.
    acpi_fans = [device for device in temps["cooling"] if "fan" in device["type"].lower()]
    state = module_state(root_modules, temps["cooling"])
    return {
        "module": state,
        "fan_service": {
            "present": bool(fan_partitions),
            "partitions": fan_partitions,
            "uuid": FAN_UUID,
        },
        "acpi_fan_surface": {
            "cooling_devices": acpi_fans,
            "hwmon_fan_channels": [channel for entry in sensors for channel in entry["fans"]],
            "hwmon_pwm_channels": [channel for entry in sensors for channel in entry["pwms"]],
            "present": bool(acpi_fans) or any(entry["fans"] or entry["pwms"] for entry in sensors),
        },
        "thermal_zones": len(temps["zones"]),
        "partitions": len(parts),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument("--ffa", type=Path, default=FFA, help=argparse.SUPPRESS)
    parser.add_argument("--thermal", type=Path, default=THERMAL, help=argparse.SUPPRESS)
    parser.add_argument("--hwmon", type=Path, default=HWMON, help=argparse.SUPPRESS)
    args = parser.parse_args()

    data = report(args.ffa, args.thermal, args.hwmon)
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        service = data["fan_service"]
        surface = data["acpi_fan_surface"]
        print(f"FF-A partitions: {data['partitions']}   thermal zones: {data['thermal_zones']}")
        print(f"fan service: {'present' if service['present'] else 'ABSENT'}"
              + (f" ({service['partitions'][0]['partition_id']}, "
                 f"{len(service['partitions'])} entry)" if service["present"] else ""))
        print(f"ACPI fan surface: {'present' if surface['present'] else 'ABSENT'}"
              f"  cooling={len(surface['cooling_devices'])}"
              f" fan={surface['hwmon_fan_channels']} pwm={surface['hwmon_pwm_channels']}")
        module = data["module"]
        if module["loaded"]:
            reading = module["devices"][0]["cur_state"] if module["devices"] else "no device"
            print(f"spark_fan module: loaded, cur_state={reading}  <- this read is the SMC read")
        elif service["present"] and not surface["present"]:
            print("spark_fan module: not loaded (insmod it and read cur_state to answer the question)")
        if service["present"] and not surface["present"]:
            print("=> the service exists but nothing exposes it: the read-only probe or the module is the "
                  "only software route, and both need an explicit go")
    return 0 if data["fan_service"]["present"] else 1


if __name__ == "__main__":
    sys.exit(main())
