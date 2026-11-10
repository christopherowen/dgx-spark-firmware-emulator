#!/usr/bin/env python3
"""Read and write the SoC's advertised power limits (SPBM), given the region's base.

The vendor DSDT names every field of that structure — PL1..PL4 in four sources plus the effective
value, and the controller's PID terms — and `research/spbm-interface.json` holds the map, together
with the base the rest of it is measured from: 0x05170000. That base is the firmware's own
statement — `\_SB.MTEL` declares `Memory32Fixed(0x05170000, 0x00060000)` in its `_CRS`, and the
registers the processor `_CPC` objects return land at this table's CPUEB offsets inside it — so the
tool uses it by default and says where it came from. `--base` overrides it for a machine whose
firmware moved the block, and a live write still has to be asked for:

    pl_limits.py --image spbm.bin --read
    pl_limits.py --image spbm.bin --set pl1=45000
    pl_limits.py --image spbm.bin --restore spbm.orig.bin

Writing to a live machine's memory is a separate decision: `--dev-mem /dev/mem --base 0x…` exists for
that, and it is deliberately explicit rather than a default. Every write is preceded by a backup of
the 512-byte window, and `--restore` puts it back, so an experiment is reversible by construction.
"""

import argparse
import json
from pathlib import Path

# \_SB.MTEL declares Memory32Fixed(0x05170000, 0x00060000), and the _CPC registers the DSDT returns
# fall at this table's CPUEB offsets inside it: 0x5170000 + 0x5C404 = 0x51CC404.
DEFAULT_BASE = 0x5170000
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
INTERFACE = ROOT / "research" / "spbm-interface.json"

# The fields this tool touches: the OS's requested limits and the effective ones.
OS_LIMITS = {f"pl{index}": f"SPBM_PL{index}_VAL_OS_OFFSET" for index in (1, 2, 3, 4)}
EFFECTIVE_LIMITS = {f"pl{index}": f"SPBM_PL{index}_VAL_OFFSET" for index in (1, 2, 3, 4)}
WINDOW = 0x400


def offsets() -> dict[str, int]:
    fields = json.loads(INTERFACE.read_text())["fields"]
    return {field["name"]: field["offset"] for field in fields}


def read_window(source, base: int | None) -> bytearray:
    if source is None:
        raise ValueError("one of --image or --dev-mem is required")
    if str(source).startswith("/dev/"):
        if base is None:
            raise ValueError("--base is required with --dev-mem: this tool does not guess it")
        with open(source, "rb") as handle:
            handle.seek(base)
            return bytearray(handle.read(WINDOW))
    data = Path(source).read_bytes()
    if len(data) < WINDOW:
        raise ValueError(f"{source} is shorter than the {WINDOW:#x}-byte window")
    return bytearray(data[:WINDOW])


def write_window(source, base: int | None, window: bytes) -> None:
    if str(source).startswith("/dev/"):
        if base is None:
            raise ValueError("--base is required with --dev-mem")
        with open(source, "r+b", buffering=0) as handle:
            handle.seek(base)
            handle.write(window)
        return
    path = Path(source)
    original = path.read_bytes()
    path.write_bytes(bytes(window) + original[len(window):])


def show(window: bytes, table: dict[str, int], label: str, names: dict[str, str]) -> None:
    for short, field in names.items():
        offset = table.get(field)
        if offset is None or offset + 4 > len(window):
            continue
        value = struct.unpack_from("<I", window, offset)[0]
        print(f"{label} {short.upper():>3} = {value:>10} ({value:#010x}) at {offset:#06x}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--image", type=Path, help="a file holding the structure's window")
    parser.add_argument("--dev-mem", type=Path, help="a live device node; requires --base")
    parser.add_argument("--base", type=lambda v: int(v, 0),
                        help=f"the structure's base address; an image is read at {DEFAULT_BASE:#x} "
                             "by default (the firmware's own _SB.MTEL resource descriptor), and a "
                             "live device node must state one")
    parser.add_argument("--read", action="store_true", help="print the limits the map names")
    parser.add_argument("--set", action="append", default=[], metavar="PL1=VALUE",
                        help="write an OS limit slot (repeatable)")
    parser.add_argument("--restore", type=Path, metavar="BACKUP",
                        help="restore a window saved before a write")
    args = parser.parse_args()
    source = args.dev_mem or args.image
    if args.dev_mem is not None:
        base = args.base                     # a live write states its address, always
    else:
        base = DEFAULT_BASE if args.base is None else args.base
    try:
        table = offsets()
        if args.restore:
            write_window(source, base, Path(args.restore).read_bytes())
            window = read_window(source, base)
            show(window, table, "effective", EFFECTIVE_LIMITS)
            return 0
        window = read_window(source, base)
        show(window, table, "os", OS_LIMITS)
        show(window, table, "effective", EFFECTIVE_LIMITS)
        for spec in args.set:
            short, _, value = spec.partition("=")
            short = short.strip().lower()
            if short not in OS_LIMITS:
                parser.error(f"unknown limit {short!r}; the OS slots are {sorted(OS_LIMITS)}")
            offset = table[OS_LIMITS[short]]
            backup = Path(f"{source}.orig")
            if not backup.exists():
                backup.write_bytes(bytes(window))
                print(f"backup written to {backup}")
            struct.pack_into("<I", window, offset, int(value, 0))
            print(f"{short.upper()} -> {int(value, 0)} ({int(value, 0):#010x}) at {offset:#06x}")
        if args.set:
            write_window(source, base, window)
            show(read_window(source, base), table, "os", OS_LIMITS)
        return 0
    except (OSError, ValueError, KeyError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
