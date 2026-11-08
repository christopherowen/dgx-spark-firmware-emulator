#!/usr/bin/env python3
"""Enumerate the FFS modules inside a UEFI firmware image and locate code in them.

A hang is usually a runtime address, not a file offset.  Matching a few bytes of
the sampled code back into the image turns that address into a module GUID and a
file offset, which is what a decompiler needs.  Modules are PE-loaded to RAM, so
the literal code bytes survive unchanged even though the address does not.
"""

from __future__ import annotations

import argparse
import struct
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FV_SIGNATURE = b"_FVH"
FV_SIGNATURE_OFFSET = 0x28
FFS_HEADER = 24
MIN_WINDOW = 24


@dataclass(frozen=True)
class Module:
    offset: int
    size: int
    kind: int
    guid: str

    @property
    def end(self) -> int:
        return self.offset + self.size

    def label(self) -> str:
        return f"{self.offset:#09x} size={self.size:#07x} type={self.kind:#04x} {self.guid}"


@dataclass(frozen=True)
class Volume:
    offset: int
    size: int


def volumes(image: bytes) -> list[Volume]:
    found = []
    offset = 0
    while (hit := image.find(FV_SIGNATURE, offset)) >= 0:
        start = hit - FV_SIGNATURE_OFFSET
        if start >= 0:
            length = struct.unpack_from("<Q", image, start + 0x20)[0]
            if 0x48 <= length <= len(image) - start:
                found.append(Volume(start, length))
        offset = hit + 1
    return discovered_dedupe(found)


def discovered_dedupe(volumes: list[Volume]) -> list[Volume]:
    seen: set[int] = set()
    result = []
    for volume in volumes:
        if volume.offset in seen:
            continue
        seen.add(volume.offset)
        result.append(volume)
    return result


def _file_size(image: bytes, cursor: int, end: int) -> int | None:
    """Return the size of a valid FFS file header at cursor, or None."""
    if cursor + FFS_HEADER > end:
        return None
    if image[cursor:cursor + 16] == b"\xff" * 16:
        return None
    # The FFS file checksum is 0xAA when a file carries none, which is almost
    # every file; a random 24-byte window rarely satisfies it, and without this
    # the walker accepts section payload bytes as headers.
    if image[cursor + 17] != 0xAA:
        return None
    kind = image[cursor + 18]
    size = int.from_bytes(image[cursor + 20:cursor + 23], "little")
    if not 0x01 <= kind <= 0x0F:
        return None
    if not FFS_HEADER <= size <= end - cursor:
        return None
    return size


def modules(image: bytes) -> list[Module]:
    found: list[Module] = []
    ordered = sorted(volumes(image), key=lambda volume: volume.offset)
    for index, volume in enumerate(ordered):
        # FV layout: length at +0x20, signature at +0x28, attributes at +0x2c,
        # header length at +0x30, extension header at +0x34.
        header = struct.unpack_from("<H", image, volume.offset + 0x30)[0]
        cursor = volume.offset + header
        # An outer volume contains the inner ones, so its own walk must stop
        # where the next volume begins or it reads that header as a file.
        end = volume.offset + volume.size
        if index + 1 < len(ordered):
            end = min(end, ordered[index + 1].offset)
        # Never shrink `end`: a valid file may end with 0xFF bytes, and trimming
        # erased space from the volume end would truncate it. Instead, jump over
        # erased runs at the cursor, which is what makes a mostly empty 32 MiB
        # volume cheap to walk.
        while cursor + FFS_HEADER <= end:
            if image[cursor:cursor + 8] == b"\xff" * 8:
                stripped = image[cursor:end].lstrip(b"\xff")
                if not stripped:
                    break
                cursor = ((end - len(stripped)) + 7) & ~7
                continue
            size = _file_size(image, cursor, end)
            if size is None:
                # Free space, padding or an alignment gap: step forward.
                cursor = (cursor + 8 + 7) & ~7
                continue
            found.append(Module(cursor, size, image[cursor + 18],
                                str(uuid.UUID(bytes_le=image[cursor:cursor + 16]))))
            cursor = (cursor + size + 7) & ~7
    return found


def module_at(image: bytes, offset: int) -> Module | None:
    for module in modules(image):
        if module.offset <= offset < module.end:
            return module
    return None


def locate(image: bytes, window: bytes, minimum: int = MIN_WINDOW) -> tuple[int, int, int] | None:
    """Find a code window in the image, trimming it until it matches exactly once.

    Returns `(offset, length, skipped)`: `skipped` bytes were trimmed from the front of the window
    before it matched, so a caller that asked about the byte at window index `i` must add
    `i - skipped` to the offset to name it. Returning only the offset would make a relocated first
    instruction silently shift every offset the caller reports.
    """
    skipped = 0
    data = window
    while len(data) >= minimum:
        first = image.find(data)
        if first >= 0 and image.find(data, first + 1) < 0:
            return first, len(data), skipped
        data = data[1:]
        skipped += 1
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--at", action="append", default=[], metavar="OFFSET",
                        help="report the module containing this image offset")
    parser.add_argument("--find", metavar="HEXBYTES",
                        help="locate a hex byte window inside the image")
    parser.add_argument("--list", action="store_true", help="list every module")
    arguments = parser.parse_args()

    image = arguments.image.read_bytes()
    print(f"volumes: {[hex(v.offset) for v in volumes(image)]}")
    if arguments.list:
        for module in modules(image):
            print(" ", module.label())
    for spec in arguments.at:
        offset = int(spec, 16)
        module = module_at(image, offset)
        print(f"{offset:#09x} -> " + (module.label() if module else "not inside any module"))
    if arguments.find:
        hit = locate(image, bytes.fromhex(arguments.find))
        if hit is None:
            print("window not found uniquely")
            return 1
        offset, length, skipped = hit
        module = module_at(image, offset)
        print(f"window matched {length} bytes at {offset:#09x} (skipped {skipped}) -> "
              + (module.label() if module else "not inside any module"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
