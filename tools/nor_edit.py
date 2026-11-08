#!/usr/bin/env python3
"""Edit an NVAR store: list variables, tombstone one, or rewrite the DMI index.

Every command writes a new file through --out and never modifies its input, so a
hardware readback can be edited without risk to the original.

Scope and limits. This tool was written against stores captured in an emulator:
bank 0 only, a bank size of 0x40000, the first record at 0x60, and the first
variable whose name matches. None of that is confirmed on arbitrary hardware, so a
result from this tool is a proposal, not a verified repair. On a real image,
establish the live bank and the record offsets first, and compare the whole image
before and after rather than a single byte.

Record layout, confirmed against a captured store:
    NVAR | size u16 | 3 state bytes | attributes u8 | index u8 | name NUL | data
Entries are packed back to back with no padding.

DMI shape: the index variable (DmiArray) holds a one-byte count followed by 255
five-byte entries at 5*(i+1).  Each record variable is named
DmiVar%02x%04x%02x%02x, which is exactly its five-byte entry.
"""

from __future__ import annotations

import argparse
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

SIGNATURE = b"NVAR"
HEADER = 11
BANK = 0x40000
FIRST_ENTRY = 0x60
INDEX_NAME = "DmiArray"
DMI_PREFIX = "DmiVar"
ENTRY = 5
ENTRY_SLOTS = 255
VALID = 0x80


@dataclass
class Variable:
    offset: int
    size: int
    attributes: int
    index: int
    name: str

    @property
    def payload(self) -> int:
        return self.offset + HEADER + len(self.name) + 1

    @property
    def valid(self) -> bool:
        return bool(self.attributes & VALID)

    def label(self) -> str:
        state = "valid" if self.valid else "invalid"
        return (f"{self.offset:#06x} size={self.size:#06x} "
                f"attributes={self.attributes:#04x} index={self.index:#04x} "
                f"{state} {self.name!r}")


def variables(data: bytes, bank: int = 0) -> list[Variable]:
    found: list[Variable] = []
    cursor = bank * BANK + FIRST_ENTRY
    # Bound by the bytes present as well as by the bank: a truncated input must stop the walk, not
    # let a record that declares a large size be "validated" against a bank that is not there and
    # then be used to fabricate an enlarged image.
    end = min((bank + 1) * BANK, len(data))
    while cursor + HEADER <= end and data[cursor:cursor + 4] == SIGNATURE:
        size = struct.unpack_from("<H", data, cursor + 4)[0]
        if size < HEADER or cursor + size > end:
            break
        name = data[cursor + HEADER:cursor + size].split(b"\x00")[0].decode("latin-1")
        found.append(Variable(cursor, size, data[cursor + 9], data[cursor + 10], name))
        cursor += size
    return found


def find(data: bytes, name: str, allow_duplicates: bool = False) -> Variable:
    matches = [variable for variable in variables(data) if variable.name == name]
    if not matches:
        raise KeyError(f"no variable named {name!r} in bank 0")
    if len(matches) > 1 and not allow_duplicates:
        offsets = ", ".join(f"{variable.offset:#06x}" for variable in matches)
        sys.exit(f"{name!r} appears {len(matches)} times in bank 0 ({offsets}); "
                 "this tool edits the first match only and will not guess — "
                 "edit the store by hand or pass a narrower target")
    return matches[0]


def dmi_entries(data: bytes) -> list[str]:
    index = find(data, INDEX_NAME)
    payload = data[index.payload:index.offset + index.size]
    count = payload[0]
    return [payload[ENTRY * (slot + 1):ENTRY * (slot + 2)].hex().upper()
            for slot in range(min(count, ENTRY_SLOTS))]


def set_dmi_entries(data: bytearray, entries: list[str]) -> None:
    if len(entries) > ENTRY_SLOTS:
        raise ValueError(f"at most {ENTRY_SLOTS} entries fit the index")
    index = find(bytes(data), INDEX_NAME)
    payload = index.payload
    length = index.size - (payload - index.offset)
    # The index lives inside its variable's allocated slot; writing past that
    # slot would silently overwrite the next variable, so refuse instead.
    if payload + length > index.offset + index.size:
        raise ValueError("index payload runs past its variable; refusing to write")
    # Slot 0 holds the count; every entry occupies one five-byte slot after it,
    # so n entries address up to ENTRY * (n + 1) bytes of the variable.
    needed = 1 if not entries else ENTRY * (len(entries) + 1)
    if needed > length:
        raise ValueError(
            f"{len(entries)} entries need {needed} bytes but the index variable "
            f"holds {length}; refusing to grow a variable in place")
    table = bytearray(length)
    table[0] = len(entries)
    for slot, entry in enumerate(entries):
        raw = bytes.fromhex(entry)
        if len(raw) != ENTRY:
            raise ValueError(f"{entry!r} is not a {ENTRY}-byte entry")
        table[ENTRY * (slot + 1):ENTRY * (slot + 2)] = raw
    data[payload:payload + length] = table


def load(source: Path) -> tuple[bytes, bytearray]:
    data = source.read_bytes()
    return data, bytearray(data)


def save(work: bytearray, destination: Path) -> None:
    if destination.exists():
        sys.exit(f"refusing to overwrite {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("xb") as handle:   # exclusive: never clobbers a file
            handle.write(bytes(work))
    except FileExistsError:
        sys.exit(f"refusing to overwrite {destination}")


def poison_findings(data: bytes) -> list[str]:
    """Report DMI records whose declared SMBIOS length is shorter than what was stored.

    The defect's store-side signature. Deliberately says nothing about boot outcomes:
    those depend on the table the firmware rebuilds, so a short declared length is
    reported as a finding, never as a prediction.
    """
    findings = []
    for variable in variables(data):
        if not variable.name.startswith(DMI_PREFIX) or not variable.valid:
            continue
        payload = variable.payload
        stored = variable.offset + variable.size - payload
        if stored <= 1:
            findings.append(f"{variable.name}: no SMBIOS header in {stored} stored bytes")
            continue
        declared = data[payload + 1]
        if declared > stored:
            findings.append(f"{variable.name} at {variable.offset:#06x}: declares {declared} "
                            f"bytes, stored {stored} -- declared exceeds stored")
        elif stored - declared > 2:
            findings.append(f"{variable.name} at {variable.offset:#06x}: declares {declared} "
                            f"bytes, stored {stored} -- slack {stored - declared}, zero run "
                            f"{stored - declared - 2}")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    listing = sub.add_parser("list", help="list variables in bank 0")
    listing.add_argument("image", type=Path)
    listing.add_argument("--dmi-only", action="store_true")

    dmi = sub.add_parser("dmi", help="show the DMI index and its records")
    dmi.add_argument("image", type=Path)

    tomb = sub.add_parser("tombstone", help="clear the valid bit on a variable")
    tomb.add_argument("image", type=Path)
    tomb.add_argument("--name", required=True)
    tomb.add_argument("--out", type=Path, required=True)

    restore = sub.add_parser("revive", help="set the valid bit on a variable")
    restore.add_argument("image", type=Path)
    restore.add_argument("--name", required=True)
    restore.add_argument("--out", type=Path, required=True)

    blank = sub.add_parser("blank", help="zero a variable's data, keeping name and attributes")
    blank.add_argument("image", type=Path)
    blank.add_argument("--name", required=True)
    blank.add_argument("--out", type=Path, required=True)

    checker = sub.add_parser("check", help="report the poison signature, without predicting boot")
    checker.add_argument("image", type=Path)

    entries = sub.add_parser("dmi-set", help="rewrite the index count and entries")
    entries.add_argument("image", type=Path)
    entries.add_argument("--entries", default="",
                         help="comma separated five-byte entries, in hex")
    entries.add_argument("--out", type=Path, required=True)

    arguments = parser.parse_args()
    data = arguments.image.read_bytes()

    if arguments.command == "list":
        for variable in variables(data):
            if arguments.dmi_only and not (variable.name == INDEX_NAME
                                           or variable.name.startswith(DMI_PREFIX)):
                continue
            print(variable.label())
        return 0

    if arguments.command == "check":
        findings = poison_findings(data)
        for finding in findings:
            print(finding)
        print(f"{len(findings)} record(s) whose declared length is shorter than what was stored")
        print("Store-side report only. Measured outcomes: slack 123 stalled at 4 checkpoints; "
              "slack 4 cleared the walk and reached the setup screen; slack 2 was not reported "
              "and those machines booted. Which of these a given store produces also depends "
              "on the table rebuilt from it, so a finding is not proof of a brick and no "
              "finding is not proof of a boot.")
        return 0

    if arguments.command == "dmi":
        index = find(data, INDEX_NAME)
        print(f"index  {index.label()}")
        payload = data[index.payload:index.offset + index.size]
        print(f"count  {payload[0]}")
        for slot, entry in enumerate(dmi_entries(data)):
            print(f"  [{slot}] {entry}")
        for variable in variables(data):
            if variable.name.startswith(DMI_PREFIX):
                print(f"record {variable.label()}")
        records = [v for v in variables(data) if v.name.startswith(DMI_PREFIX) and v.valid]
        indexed = set(dmi_entries(data))
        for variable in records:
            if variable.name[len(DMI_PREFIX):] not in indexed:
                print(f"  valid record not in the index: {variable.name}")
        return 0

    data, work = load(arguments.image)
    if arguments.command == "blank":
        target = find(data, arguments.name)
        work[target.payload:target.offset + target.size] = bytes(target.offset + target.size
                                                                - target.payload)
        save(work, arguments.out)
        print(f"blanked {arguments.name!r} at {target.offset:#06x} -> {arguments.out}")
        return 0

    if arguments.command in ("tombstone", "revive"):
        target = find(data, arguments.name)
        work[target.offset + 9] = (target.attributes & ~VALID if arguments.command == "tombstone"
                                   else target.attributes | VALID)
        save(work, arguments.out)
        print(f"{arguments.command} {arguments.name!r} at {target.offset:#06x} "
              f"-> {arguments.out}")
        return 0

    wanted = [item.strip() for item in arguments.entries.split(",") if item.strip()]
    set_dmi_entries(work, wanted)
    save(work, arguments.out)
    print(f"dmi index set to {len(wanted)} entries -> {arguments.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
