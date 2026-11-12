#!/usr/bin/env python3
"""Where the timing-table partitions live in a capsule, and whether they carry anything.

The component map's offsets are relative to a **base** (the slot's declared header position), not to
the file; read without the base, a populated region can look erased. What a local capsule contains
stays in the ignored `firmware/` directory.

The vendor's capsule map names the partitions that would hold boot configuration and DRAM data
(`BCT`, `DDRFSK`, `MISC`). This tool reports, for any image that carries that map, whether those
regions are populated — and if a readback ever supplies them, extracts them with their hashes so
they can be compared between two units or two firmware versions.

It does not decode DRAM timings. Non-FF bytes are not proof of decoded parameters, and FF in one
region is not proof that no configuration exists elsewhere; what the tool reports is which of the three
regions contain anything, at the coordinates a base is supplied for.

usage:
    timing_tables.py image.bin
    timing_tables.py image.bin --extract firmware/runs/<id>
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.container import ContainerError, header_position, table  # noqa: E402

# The partitions that would carry boot configuration and DRAM data, by name in the vendor map.
TIMING_PARTITIONS = ("BCT", "DDRFSK", "MISC")


def find_map(image: bytes, slot: int = 0x2000000) -> int:
    """Offset of the SoC firmware image the map describes, inside a whole-device image.

    A device image holds two 32 MiB halves; the second starts with the SF_BOOT container, the first
    carries a header before it. The map is found by its magic rather than assumed.
    """
    for candidate in (slot, 0, 0x100000):
        if image[candidate:candidate + 8] == b"SF_BOOT\x00":
            return candidate
    raise ContainerError("no SF_BOOT container in this image")


# The map's offsets are relative to the boot-partition table, which sits 0x203000 into the slot
# image, not to the container. Reading without this base makes BCT look erased: at container+offset
# the bytes belong to something else, and every partition shifts by 0x203000.
BPT_BASE = 0x203000          # documented default only; the base used is derived from the header


def scan_for_bct(image: bytes) -> list[dict]:
    """Every `BCT` structure in the image, with the patch records that follow it.

    A coordinate system is an assumption; a magic search is not. This reports what is actually in the
    image, wherever the layout put it.
    """
    found = []
    index = image.find(b"BCT\x00")
    while index >= 0:
        records, cursor = [], index
        while True:
            cursor = image.find(b"BCTV", cursor + 1)
            if cursor < 0 or cursor > index + 0x4000:
                break
            records.append(image[cursor + 8:cursor + 20].rstrip(b"\x00").decode("ascii", "replace"))
        found.append({"offset": index, "records": records})
        index = image.find(b"BCT\x00", index + 1)
    return found


def report(image_path: Path) -> dict:
    image = image_path.read_bytes()
    base = find_map(image)                      # the slot image: SF_BOOT's position
    container = image[base:]
    bpt = header_position(container)            # the map header inside it: the offsets' own base
    components = table(container)
    parts = []
    for component in components:
        if component.name not in TIMING_PARTITIONS:
            continue
        region = container[bpt + component.offset:bpt + component.end]
        populated = set(region) != {0xFF}
        parts.append({
            "name": component.name,
            "offset": component.offset,
            "size": component.size,
            "populated": populated,
            "sha256": hashlib.sha256(region).hexdigest() if populated else None,
        })
    return {
        "image": str(image_path),
        "image_sha256": hashlib.sha256(image).hexdigest(),
        "container_offset": base,
        "bpt_base": bpt,          # the base the table's offsets are relative to
        "bct_structures": scan_for_bct(image),
        "partitions": parts,
        # A populated region is not decoded timings: the report says so rather than implying it.
        "carries_data": any(part["populated"] for part in parts),
        "note": "populated means non-FF bytes in the named region, nothing more; decoding is not done here",
    }


def extract(image_path: Path, report_data: dict, destination: Path) -> list[Path]:
    image = image_path.read_bytes()
    base = report_data["container_offset"]        # the slot image
    bpt = report_data["bpt_base"]                 # the map header inside it: the offsets' base
    written = []
    destination.mkdir(parents=True, exist_ok=True)
    for part in report_data["partitions"]:
        if not part["populated"]:
            continue
        start = base + bpt + part["offset"]
        target = destination / f"{part['name']}.bin"
        target.write_bytes(image[start:start + part["size"]])
        written.append(target)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("image", type=Path)
    parser.add_argument("--extract", type=Path,
                        help="write the populated partitions here (must live under firmware/)")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args()
    try:
        data = report(args.image)
    except (OSError, ContainerError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    if args.extract:
        allowed = (ROOT / "firmware").resolve()
        destination = args.extract.resolve()
        if allowed not in destination.parents:
            print(f"error: {args.extract} must live under firmware/", file=sys.stderr)
            return 1
        written = extract(args.image, data, destination)
        data["extracted"] = [str(path) for path in written]
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        for part in data["partitions"]:
            state = "populated" if part["populated"] else "erased in this artifact"
            print(f"{part['name']:8} {part['size']:#010x} at {part['offset']:#010x}: {state}")
        print("carries data" if data["carries_data"] else "no data in this artifact")
    return 0


if __name__ == "__main__":
    sys.exit(main())
