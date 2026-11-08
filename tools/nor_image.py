#!/usr/bin/env python3
"""Cheap structural validation and staging for a vendor NVAR flash image.

A hardware owner may supply a 32 MiB readback of the boot flash.  Before any
emulator run touches it we check that it is the right size and that its
firmware-volume and NVAR entry structure is coherent, then stage a copy so the
original is never opened for writing.  This is a sanity gate, not a parser:
it deliberately does not interpret variable payloads.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path

# The emulator's default model is 32 MiB because every stored arm was taken on it;
# the board's own boot flash is 64 MiB (MX77U51255FZ4I42). A hardware readback is
# therefore accepted at either size, and the model used for a run selects the match.
MODEL_SIZE = 0x2000000          # 32 MiB emulator model
PART_SIZE = 0x4000000           # 64 MiB MX77U51255FZ4I42
IMAGE_SIZE = MODEL_SIZE
BANK_END = (0x40000, 0x80000)   # the two banks the vendor driver reports
FV_SIGNATURE = b"_FVH"
FV_OFFSETS = (0x28, 0x40028)    # signature sits 0x28 into each volume header
NVAR_SIGNATURE = b"NVAR"
NVAR_HEADER = 10
FIRST_ENTRY = 0x60


class ImageError(ValueError):
    """The image is not a usable vendor flash readback."""


@dataclass(frozen=True)
class Bank:
    index: int
    start: int
    end: int
    header_offset: int | None
    entries: int
    last_entry_end: int


@dataclass(frozen=True)
class Report:
    size: int
    sha256: str
    banks: tuple[Bank, ...]

    @property
    def entries(self) -> int:
        return sum(bank.entries for bank in self.banks)


def _entry(data: bytes, offset: int, limit: int) -> int:
    """Validate one NVAR entry header and return its size, or raise."""
    if offset + NVAR_HEADER > limit:
        raise ImageError(f"truncated entry header at {offset:#x}")
    if data[offset:offset + 4] != NVAR_SIGNATURE:
        raise ImageError(f"missing NVAR signature at {offset:#x}")
    size = int.from_bytes(data[offset + 4:offset + 6], "little")
    attributes = data[offset + 9]
    if size < NVAR_HEADER:
        raise ImageError(f"entry at {offset:#x} declares size {size}")
    if offset + size > limit:
        raise ImageError(f"entry at {offset:#x} runs past its bank")
    if attributes in (0x00, 0xFF):
        raise ImageError(f"entry at {offset:#x} has dead attributes {attributes:#04x}")
    return size


def _walk_bank(data: bytes, index: int, start: int, end: int) -> Bank:
    header = None
    for candidate in FV_OFFSETS:
        if start <= candidate < end and data[candidate:candidate + 4] == FV_SIGNATURE:
            header = candidate
            break
    if header is None:
        return Bank(index, start, end, None, 0, start)

    count = 0
    cursor = start + FIRST_ENTRY
    while cursor + NVAR_HEADER <= end and data[cursor:cursor + 4] == NVAR_SIGNATURE:
        size = _entry(data, cursor, end)
        cursor += size
        count += 1
        if count > 512:
            raise ImageError(f"bank {index} has an implausible entry count")
    return Bank(index, start, end, header, count, cursor)


def inspect(path: Path) -> Report:
    """Validate a 32 MiB vendor image and report its bank structure."""
    path = Path(path)
    if not path.is_file():
        raise ImageError(f"not a regular file: {path}")
    size = path.stat().st_size
    if size not in (MODEL_SIZE, PART_SIZE):
        raise ImageError(f"image is {size} bytes; the model needs {MODEL_SIZE} and the "
                         f"board's part {PART_SIZE}")
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()

    banks = (
        _walk_bank(data, 0, 0, BANK_END[0]),
        _walk_bank(data, 1, BANK_END[0], BANK_END[1]),
    )
    if all(bank.header_offset is None for bank in banks):
        raise ImageError("no firmware-volume signature at either bank offset")
    if banks[0].header_offset is not None and banks[0].entries == 0:
        raise ImageError("bank 0 has a volume header but no NVAR entries")
    return Report(size=size, sha256=digest, banks=banks)


def lift(source: Path, destination: Path) -> Path:
    """Place a single-bank capture into the whole part without altering its bytes.

    The variable region sits at the same offset inside bank 0 in both layouts, so
    the copy is a lift of exactly one bank into an erased whole-part image plus a
    blank spare bank. Any other length is refused rather than truncated: a
    multi-bank capture would lose data and a short one is not a bank.
    """
    source = Path(source)
    original = source.read_bytes()
    if len(original) != BANK_END[0]:
        raise ImageError(
            f"{source} is {len(original)} bytes; a lift takes exactly one "
            f"{BANK_END[0]}-byte bank")
    image = bytearray(b"\xff" * IMAGE_SIZE)
    image[:BANK_END[0]] = original[:BANK_END[0]]
    image[BANK_END[1] - BANK_END[0] + FV_OFFSETS[0]:BANK_END[1] - BANK_END[0] + FV_OFFSETS[0] + 4] = FV_SIGNATURE
    destination = Path(destination)
    if destination.exists():
        raise ImageError(f"refusing to overwrite {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Validate the composed image before it is published, so a rejected lift
    # never leaves a file behind.
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
        candidate = Path(directory) / "candidate.bin"
        candidate.write_bytes(bytes(image))
        inspect(candidate)
    try:
        with destination.open("xb") as handle:   # exclusive: never clobbers a file
            handle.write(bytes(image))
    except FileExistsError:
        raise ImageError(f"refusing to overwrite {destination}") from None
    return destination


def stage(source: Path, destination: Path) -> tuple[str, str]:
    """Copy a validated image to a working file. Never overwrites.

    Returns (source_sha256, destination_sha256); they must be equal, and the
    caller is expected to treat the destination as the mutable state file.
    """
    source, destination = Path(source), Path(destination)
    if destination.exists():
        raise ImageError(f"refusing to overwrite {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    report = inspect(source)
    try:
        with source.open("rb") as reader, destination.open("xb") as writer:
            shutil.copyfileobj(reader, writer)
    except FileExistsError:
        raise ImageError(f"refusing to overwrite {destination}") from None
    staged = hashlib.sha256(destination.read_bytes()).hexdigest()
    if staged != report.sha256:
        destination.unlink(missing_ok=True)
        raise ImageError("staged copy does not match the source")
    return report.sha256, staged


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    inspect_cmd = sub.add_parser("inspect", help="validate an image and print its banks")
    inspect_cmd.add_argument("image", type=Path)
    lift_cmd = sub.add_parser("lift", help="place a single-bank capture into a whole part")
    lift_cmd.add_argument("image", type=Path)
    lift_cmd.add_argument("output", type=Path)
    stage_cmd = sub.add_parser("stage", help="copy a validated image to a working file")
    stage_cmd.add_argument("image", type=Path)
    stage_cmd.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.command in ("lift", "stage"):
        try:
            produce = lift if args.command == "lift" else stage
            result = produce(args.image, args.output)
        except (ImageError, OSError) as error:
            print(f"rejected: {error}")
            return 1
        print(f"{args.command}ed {args.image} -> {result}")
        return 0
    try:
        report = inspect(args.image)
    except ImageError as error:
        print(f"rejected: {error}")
        return 1
    print(f"size      {report.size} bytes")
    print(f"sha256    {report.sha256}")
    for bank in report.banks:
        state = "volume header" if bank.header_offset is not None else "no volume header"
        print(f"bank {bank.index}  [{bank.start:#08x},{bank.end:#08x})  {state}"
              f"  entries {bank.entries}  ends {bank.last_entry_end:#08x}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
