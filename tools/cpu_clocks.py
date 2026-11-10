#!/usr/bin/env python3
"""Read and cap the CPU frequency range the firmware advertises, through the kernel's interface.

The vendor DSDT publishes Continuous Performance Control on every processor device (`_CPC`, standard
`ACPI0007`), with SystemMemory registers inside the block `\\_SB.MTEL` declares at 0x05170000 — so the
OS owns this lever: `cppc_cpufreq` reads those registers and exposes
`/sys/devices/system/cpu/cpufreq/policy*/`, which is what this tool uses. Nothing here writes a
vendor register or needs a kernel module, and nothing pins a frequency table: the ranges come from
the firmware through the kernel, so a later firmware with different limits keeps working.

usage:
    cpu_clocks.py status                     # driver, governor, ranges, current, EPP
    cpu_clocks.py cap 3000000                # cap every policy at 3000 MHz
    cpu_clocks.py cap max                    # remove the cap (the firmware's own ceiling)
    cpu_clocks.py epp performance            # energy/performance preference, where published
    cpu_clocks.py restore                    # put back the caps saved before the last change

A cap above the firmware's `cpuinfo_max_freq` is refused rather than clamped: anything larger is a
number the hardware does not have, and silently writing the maximum instead would report a limit
this tool did not set.
"""

import argparse
import json
from pathlib import Path
import sys

CPUFREQ = Path("/sys/devices/system/cpu/cpufreq")
BACKUP_NAME = ".cpu_clocks_backup.json"


def policies(root=CPUFREQ):
    """Every cpufreq policy the kernel published, with the files this tool needs."""
    found = []
    base = Path(root)
    if not base.is_dir():
        return found
    for entry in sorted(base.glob("policy*")):
        def read(name, cast=str):
            path = entry / name
            try:
                return cast(path.read_text().strip())
            except (OSError, ValueError):
                return None

        policy = {
            "path": entry,
            "name": entry.name,
            "driver": read("scaling_driver"),
            "governor": read("scaling_governor"),
            "cpuinfo_min": read("cpuinfo_min_freq", int),
            "cpuinfo_max": read("cpuinfo_max_freq", int),
            "scaling_min": read("scaling_min_freq", int),
            "scaling_max": read("scaling_max_freq", int),
            "cur": read("scaling_cur_freq", int),
            "epp": read("energy_performance_preference"),
            "epp_available": read("energy_performance_available_preferences"),
        }
        found.append(policy)
    return found


def describe(policy):
    epp = f" epp={policy['epp']}" if policy["epp"] else ""
    return (f"{policy['name']}: driver={policy['driver']} governor={policy['governor']} "
            f"range={policy['scaling_min']}..{policy['scaling_max']} of "
            f"{policy['cpuinfo_min']}..{policy['cpuinfo_max']} kHz now={policy['cur']}{epp}")


def status(found):
    if not found:
        print("no cpufreq policy found: this kernel has no CPU frequency control, and nothing "
              "was changed")
        return 1
    for policy in found:
        print(describe(policy))
    return 0


def cap_value(policy, requested):
    """The frequency to write into `scaling_max_freq`."""
    if requested == "max":
        return policy["cpuinfo_max"]
    if requested > policy["cpuinfo_max"]:
        raise ValueError(f"{requested} kHz is above this CPU's cpuinfo_max_freq "
                         f"({policy['cpuinfo_max']} kHz): the firmware does not offer it")
    if policy["cpuinfo_min"] is not None and requested < policy["cpuinfo_min"]:
        raise ValueError(f"{requested} kHz is below cpuinfo_min_freq ({policy['cpuinfo_min']} kHz)")
    return requested


def save_backup(found, root):
    state = {policy["name"]: {"scaling_max": policy["scaling_max"],
                              "epp": policy["epp"]} for policy in found}
    (Path(root) / BACKUP_NAME).write_text(json.dumps(state, indent=2) + "\n")


def set_cap(found, requested, root=CPUFREQ, dry_run=False):
    writes = []
    for policy in found:
        value = cap_value(policy, requested)
        writes.append((policy, value))
    if not dry_run:
        save_backup(found, root)
        for policy, value in writes:
            (policy["path"] / "scaling_max_freq").write_text(f"{value}\n")
    for policy, value in writes:
        print(f"{policy['name']}: scaling_max_freq {policy['scaling_max']} -> {value} kHz"
              f"{' [dry run]' if dry_run else ''}")
    return 0


def set_epp(found, preference, root=CPUFREQ, dry_run=False):
    changed = 0
    for policy in found:
        available = (policy["epp_available"] or "").split()
        if not (policy["path"] / "energy_performance_preference").exists():
            print(f"{policy['name']}: this policy publishes no energy_performance_preference")
            continue
        if available and preference not in available:
            print(f"{policy['name']}: {preference} is not one of {' '.join(available)}")
            continue
        if not dry_run:
            (policy["path"] / "energy_performance_preference").write_text(f"{preference}\n")
        print(f"{policy['name']}: energy_performance_preference {policy['epp']} -> {preference}"
              f"{' [dry run]' if dry_run else ''}")
        changed += 1
    return 0 if changed else 1


def restore(found, root=CPUFREQ, dry_run=False):
    backup_path = Path(root) / BACKUP_NAME
    if not backup_path.is_file():
        print(f"{backup_path} does not exist: nothing has been saved on this machine")
        return 1
    state = json.loads(backup_path.read_text())
    restored = 0
    for policy in found:
        saved = state.get(policy["name"])
        if not saved:
            continue
        if saved.get("scaling_max") is not None:
            if not dry_run:
                (policy["path"] / "scaling_max_freq").write_text(f"{saved['scaling_max']}\n")
            print(f"{policy['name']}: scaling_max_freq -> {saved['scaling_max']} kHz"
                  f"{' [dry run]' if dry_run else ''}")
            restored += 1
        if saved.get("epp"):
            if not dry_run:
                (policy["path"] / "energy_performance_preference").write_text(f"{saved['epp']}\n")
            print(f"{policy['name']}: energy_performance_preference -> {saved['epp']}"
                  f"{' [dry run]' if dry_run else ''}")
    return 0 if restored else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=CPUFREQ, help=f"cpufreq root (default {CPUFREQ})")
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="show the policies and their ranges")
    cap = sub.add_parser("cap", help="cap the maximum frequency, in kHz, or 'max'")
    cap.add_argument("frequency")
    epp = sub.add_parser("epp", help="set the energy/performance preference")
    epp.add_argument("preference")
    sub.add_parser("restore", help="put back the caps saved before the last change")
    args = parser.parse_args(argv)

    found = policies(args.root)
    if args.command == "status":
        return status(found)
    if not found:
        return status(found)
    if args.command == "restore":
        return restore(found, args.root, args.dry_run)
    try:
        if args.command == "cap":
            requested = args.frequency if args.frequency == "max" else int(args.frequency)
            return set_cap(found, requested, args.root, args.dry_run)
        return set_epp(found, args.preference, args.root, args.dry_run)
    except ValueError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
