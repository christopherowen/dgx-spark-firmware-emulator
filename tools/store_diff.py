#!/usr/bin/env python3
"""Compare two NVAR stores variable by variable.

Two runs of the same sequence must produce the same variables.  A diff that only
reports offsets is useless for that question, so this compares by name, then by
content, and prints the first differing byte of any variable that differs.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.nor_edit import BANK, FIRST_ENTRY, Variable, variables


def uncovered(data: bytes) -> list[bytes]:
    """Everything in each bank that is not a record: headers, table areas, free space.

    A record's meaning can live outside its own bytes — the store keeps its tables there, and a
    record names an entry in them — so comparing records alone can call two different stores equal.
    Whatever those regions are, they are compared byte for byte here.
    """
    covered = [set() for _ in range(2)]
    for bank in (0, 1):
        for variable in variables(data, bank):
            covered[bank].update(range(variable.offset, variable.offset + variable.size))
    parts = [bytes(byte for index, byte in enumerate(data[bank * BANK:(bank + 1) * BANK],
                                                     start=bank * BANK) if index not in covered[bank])
             for bank in (0, 1)]
    # Everything past the two banks belongs to the same image — a store-only file ends here, a whole
    # device continues into the code volume — and a comparison that stopped at the second bank would
    # call two different images identical.
    parts.append(data[2 * BANK:])
    return parts


def collect(data: bytes) -> dict[tuple[int, str], list[tuple[Variable, bytes]]]:
    """Every record, from both banks, grouped by (bank, name).

    The store has two banks and the firmware switches between them, so comparing bank 0 alone can
    call two different stores identical. Duplicate names are kept as a list: a valid record followed
    by an invalid one of the same name is a real difference, and a dict would hide it.
    """
    found: dict[tuple[int, str], list[tuple[Variable, bytes]]] = {}
    for bank in (0, 1):
        for variable in variables(data, bank):
            raw = data[variable.offset:variable.offset + variable.size]
            found.setdefault((bank, variable.name), []).append((variable, raw))
    return found


def first_difference(left: bytes, right: bytes) -> str:
    for index, (a, b) in enumerate(zip(left, right)):
        if a != b:
            return f"first difference at +{index:#x}: {a:#04x} vs {b:#04x}"
    if len(left) != len(right):
        return f"length differs: {len(left)} vs {len(right)}"
    return "identical"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path)
    parser.add_argument("--quiet", action="store_true", help="only report differences")
    arguments = parser.parse_args()

    # One read per input: reopening a path can mix two generations of the same file and compare a
    # store with something that was never on disk beside it.
    left_data, right_data = Path(arguments.left).read_bytes(), Path(arguments.right).read_bytes()
    left, right = collect(left_data), collect(right_data)
    left_tables, right_tables = uncovered(left_data), uncovered(right_data)
    table_differences = [bank for bank, (a, b) in enumerate(zip(left_tables, right_tables)) if a != b]
    if len(left_tables) != len(right_tables):
        table_differences.append(min(len(left_tables), len(right_tables)))
    only_left = sorted(set(left) - set(right))
    only_right = sorted(set(right) - set(left))
    differing = [key for key in sorted(set(left) & set(right))
                 if [raw for _, raw in left[key]] != [raw for _, raw in right[key]]]

    if not arguments.quiet:
        print(f"{arguments.left}: {len(left)} variables, "
              f"{hashlib.sha256(left_data).hexdigest()[:16]}")
        print(f"{arguments.right}: {len(right)} variables, "
              f"{hashlib.sha256(right_data).hexdigest()[:16]}")
    for bank, name in only_left:
        print(f"  only in left : bank {bank} {name!r} at {left[(bank, name)][0][0].offset:#06x}")
    for bank, name in only_right:
        print(f"  only in right: bank {bank} {name!r} at {right[(bank, name)][0][0].offset:#06x}")
    for key in differing:
        bank, name = key
        lpairs, rpairs = left[key], right[key]
        if len(lpairs) != len(rpairs):
            print(f"  differs      : bank {bank} {name!r}: {len(lpairs)} record(s) vs "
                  f"{len(rpairs)} (duplicates are records, not one variable)")
            continue
        for index, ((lvar, lraw), (rvar, rraw)) in enumerate(zip(lpairs, rpairs)):
            suffix = f" occurrence {index + 1}" if len(lpairs) > 1 else ""
            print(f"  differs      : bank {bank} {name!r} at {lvar.offset:#06x}/{rvar.offset:#06x} "
                  f"({len(lraw)} vs {len(rraw)} bytes){suffix} - {first_difference(lraw, rraw)}")

    for bank in table_differences:
        print(f"  differs      : bank {bank} outside the records (header, tables, free space) - "
              f"{first_difference(left_tables[bank], right_tables[bank])}")

    identical = not (only_left or only_right or differing or table_differences)
    print("identical" if identical else
          f"{len(only_left)} only-left, {len(only_right)} only-right, {len(differing)} differing, "
          f"{len(table_differences)} table")
    return 0 if identical else 1


if __name__ == "__main__":
    sys.exit(main())
