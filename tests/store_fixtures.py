"""Builders for store images that need no vendor bytes.

Every fixture this module produces is an original file: an erased part with the
volume header the measured store carries at each bank, and NVAR records packed back to
back from `0x60`, which is the shape the parsers and the firmware expect. The header is
erased apart from the volume length at `0x20` and `_FVH` at `0x28` — the real store has
no checksum there, so a fixture that built one would be stricter than the format.

These fixtures stand in for the *format*, never for a captured store whose boot
outcome was measured — that connection belongs to the lab's own artifacts.
"""

from __future__ import annotations

import struct

BANK = 0x40000
BANKS = 2
FIRST_ENTRY = 0x60
HEADER_LENGTH = FIRST_ENTRY
MODEL_SIZE = 0x2000000
PART_SIZE = 0x4000000

FV_SIGNATURE = b"_FVH"
FS_GUID = bytes(range(16, 0, -1))  # an original GUID, not a vendor one


def entry(name: str, value: bytes, attributes: int = 0x83, index: int = 0) -> bytes:
    """One NVAR record: signature, u16 size, three state bytes, attributes, index, name, data."""
    body = bytes([index]) + name.encode("ascii") + b"\x00" + value
    size = 10 + len(body)
    return b"NVAR" + size.to_bytes(2, "little") + b"\xff\xff\xff" + bytes([attributes]) + body


def fv_header(block_length: int = BANK, length: int = BANK) -> bytes:
    """The volume header this store format actually carries.

    Measured on a vendor readback: everything before `0x20` is erased, `0x20` holds the
    volume length, `0x28` the `_FVH` signature, and the rest of the header is `0xff` —
    there is no checksummed EDK2 header here, so a fixture that builds one would be
    stricter than the format and a validator that demanded one would reject the real
    thing.
    """
    header = bytearray(b"\xff" * HEADER_LENGTH)
    header[0x20:0x28] = struct.pack("<Q", length)
    header[0x28:0x2C] = FV_SIGNATURE
    header[0x2C:0x30] = b"\xff\xff\xff\xff"
    header[0x30:0x32] = struct.pack("<H", 0x48)   # header length, as measured
    header[0x32:0x34] = b"\xff\xff"
    header[0x34:0x38] = b"\x00\x00\xff\xff"
    return bytes(header)


def bank(records: list[bytes] = (), block_length: int = BANK) -> bytes:
    """One bank: a valid volume header, records from `0x60`, `0xff` after them."""
    data = bytearray(b"\xff" * block_length)
    data[0:HEADER_LENGTH] = fv_header(block_length, block_length)
    cursor = FIRST_ENTRY
    for record in records:
        if cursor + len(record) > block_length:
            raise ValueError("records do not fit in one bank")
        data[cursor:cursor + len(record)] = record
        cursor += len(record)
    return bytes(data)


def image(records: list[bytes] = (), second_bank: list[bytes] = (),
          size: int = MODEL_SIZE) -> bytes:
    """A whole store image: bank 0 carries `records`, bank 1 carries `second_bank`."""
    if size not in (MODEL_SIZE, PART_SIZE):
        raise ValueError(f"store images are {MODEL_SIZE:#x} or {PART_SIZE:#x} bytes")
    data = bytearray(b"\xff" * size)
    first = bank(records)
    data[0:len(first)] = first
    if size >= 2 * BANK:
        second = bank(second_bank)
        data[BANK:BANK + len(second)] = second
    return bytes(data)


def dmi_index(count: int = 0) -> bytes:
    """The DMI index record the store editor rewrites: one count byte and a slot table."""
    from tools.nor_edit import ENTRY_SLOTS, INDEX_NAME

    return entry(INDEX_NAME, bytes([count]) + b"\x00" * (ENTRY_SLOTS * 2 - 1), attributes=0x83)
