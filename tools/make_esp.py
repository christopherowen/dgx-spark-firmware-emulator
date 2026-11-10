"""Build a FAT32 image the profile's firmware can boot from.

The recovery image normally boots through grub on a USB stick. To start that kernel under the
profile, the kernel and its initrd have to be on a volume the firmware can read — a FAT32 file
system on the emulated NVMe namespace. This writes one from files given on the command line,
without mkfs or mtools.

Two things about FAT32 are easy to get wrong and both are enforced here rather than assumed:

* A volume is FAT32 only if it has at least 65525 clusters. mtools and `mkfs.vfat` will make and
  read smaller ones anyway; a UEFI driver will not, which is how an image can look correct, be
  readable by every host tool, and still present no volume to the firmware.
* The FAT has to describe every cluster, so the volume is sized from the cluster count, not the
  other way round; the size given on the command line is a minimum.

`tests/test_make_esp.py` reads the image back with an independent parser and checks the count.
"""

import argparse
import struct
import sys
from pathlib import Path

SECTOR = 512              # cluster size too: small clusters keep the minimum volume small
MIN_CLUSTERS = 65525      # the FAT32 floor; below this a volume is not FAT32
RESERVED_SECTORS = 32
FATS = 2
ROOT_DIR_CLUSTER = 2
DIRECTORY_ENTRY = 32
ENTRIES_PER_SECTOR = SECTOR // DIRECTORY_ENTRY


def short_name(name):
    """`VMLINUZ.IMG` -> (`VMLINUZ`, `IMG`); a name that cannot be a short name is refused."""
    stem, _, suffix = name.partition(".")
    stem, suffix = stem.upper(), suffix.upper()
    if not stem or len(stem) > 8 or len(suffix) > 3:
        raise ValueError(f"{name!r} is not an 8.3 name")
    if not all(character.isalnum() or character in "_-~" for character in stem + suffix):
        raise ValueError(f"{name!r} has characters a short name cannot carry")
    return stem, suffix


def layout(payload_bytes, entries, minimum_bytes):
    """Sectors, FAT sectors and data clusters for a volume that holds the payload."""
    clusters = max(MIN_CLUSTERS, (payload_bytes + SECTOR - 1) // SECTOR + 2)
    while True:
        fat_sectors = (clusters + 2) * 4 // SECTOR + 1
        sectors = clusters + RESERVED_SECTORS + FATS * fat_sectors
        if sectors * SECTOR >= minimum_bytes:
            return sectors, fat_sectors, clusters
        clusters = -(-(minimum_bytes // SECTOR - RESERVED_SECTORS - FATS * fat_sectors))


def directory_entry(name, attributes, cluster, size):
    stem, suffix = short_name(name) if name else ("", "")
    entry = bytearray(b" " * DIRECTORY_ENTRY)
    entry[0:len(stem)] = stem.encode("ascii")
    entry[8:8 + len(suffix)] = suffix.encode("ascii")
    entry[11] = attributes
    struct.pack_into("<H", entry, 20, cluster >> 16)
    struct.pack_into("<H", entry, 26, cluster & 0xffff)
    struct.pack_into("<I", entry, 28, size)
    return bytes(entry)


def build(files, minimum_bytes, label="SPARKESP"):
    """`files` is a list of (name, bytes); `minimum_bytes` is a floor, not a promise."""
    for name, _ in files:
        short_name(name)
    payload_bytes = sum(len(payload) for _, payload in files)
    sectors, fat_sectors, clusters = layout(payload_bytes, len(files), minimum_bytes)
    needed = sum((len(payload) + SECTOR - 1) // SECTOR for _, payload in files)
    needed += -(-DIRECTORY_ENTRY * (len(files) + 1) // SECTOR)
    if clusters < needed:
        raise ValueError(f"{len(payload_bytes)} bytes of files need {needed} clusters; "
                         f"this volume describes {clusters}")
    size = sectors * SECTOR
    image = bytearray(size)

    boot = image
    boot[0:3] = b"\xeb\x58\x90"
    boot[3:11] = b"SPARKLAB"
    struct.pack_into("<H", boot, 11, SECTOR)
    boot[13] = 1                                  # sectors per cluster
    struct.pack_into("<H", boot, 14, RESERVED_SECTORS)
    boot[16] = FATS
    boot[21] = 0xf8
    struct.pack_into("<H", boot, 24, 32)          # sectors per track: a hint, not geometry
    struct.pack_into("<H", boot, 26, 2)           # heads
    struct.pack_into("<I", boot, 32, sectors)
    struct.pack_into("<I", boot, 36, fat_sectors)
    struct.pack_into("<I", boot, 44, ROOT_DIR_CLUSTER)
    struct.pack_into("<H", boot, 48, 1)           # FSInfo sector
    struct.pack_into("<H", boot, 50, 6)           # backup boot sector
    boot[64] = 0x80
    boot[66] = 0x29
    struct.pack_into("<I", boot, 67, 0x1234abcd)
    boot[71:82] = label.encode("ascii")[:11].ljust(11)
    boot[82:90] = b"FAT32   "
    boot[510] = 0x55
    boot[511] = 0xaa

    # What the specification asks to sit beside the boot sector: its backup copy and an FSInfo
    # sector. A UEFI FAT driver checks for both; mtools does not, which is why an image mtools
    # reads happily can still be refused by firmware.
    image[6 * SECTOR:7 * SECTOR] = boot[0:SECTOR]
    info = bytearray(SECTOR)
    struct.pack_into("<I", info, 0, 0x41615252)
    struct.pack_into("<I", info, 484, 0x61417272)
    struct.pack_into("<I", info, 488, 0xffffffff)
    struct.pack_into("<I", info, 492, 0xffffffff)
    struct.pack_into("<I", info, 508, 0xaa550000)
    image[SECTOR:2 * SECTOR] = info

    fat_offset = RESERVED_SECTORS * SECTOR
    data_offset = (RESERVED_SECTORS + FATS * fat_sectors) * SECTOR

    def set_fat(cluster, value):
        for table in range(FATS):
            struct.pack_into("<I", image, fat_offset + table * fat_sectors * SECTOR + cluster * 4,
                             value)

    # The first two FAT entries are reserved and carry the media descriptor.
    for table in range(FATS):
        base = fat_offset + table * fat_sectors * SECTOR
        struct.pack_into("<I", image, base, 0x0ffffff8)
        struct.pack_into("<I", image, base + 4, 0x0fffffff)

    directory_sectors = -(-DIRECTORY_ENTRY * (len(files) + 1) // SECTOR)
    directory_clusters = -(-directory_sectors // (SECTOR // SECTOR))
    for index in range(directory_clusters):
        set_fat(ROOT_DIR_CLUSTER + index,
                0x0fffffff if index == directory_clusters - 1 else ROOT_DIR_CLUSTER + index + 1)
    next_cluster = ROOT_DIR_CLUSTER + directory_clusters

    entries = bytearray()
    for name, payload in files:
        clusters_needed = max(1, -(-len(payload) // SECTOR))
        first = next_cluster
        entries += directory_entry(name, 0x20, first, len(payload))
        for index in range(clusters_needed):
            cluster = first + index
            set_fat(cluster, 0x0fffffff if index == clusters_needed - 1 else cluster + 1)
            start = index * SECTOR
            offset = data_offset + (cluster - 2) * SECTOR
            image[offset:offset + len(payload[start:start + SECTOR])] = payload[start:start + SECTOR]
        next_cluster += clusters_needed
    entries += directory_entry("", 0x00, 0, 0)

    root_offset = data_offset + (ROOT_DIR_CLUSTER - 2) * SECTOR
    image[root_offset:root_offset + len(entries)] = entries
    return bytes(image)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="image to write")
    parser.add_argument("--add", action="append", default=[], metavar="NAME=PATH",
                        help="place PATH on the volume as the short name NAME (repeatable)")
    parser.add_argument("--size", type=int, default=0,
                        help="minimum size in bytes; the volume grows to stay FAT32")
    args = parser.parse_args(argv)

    if not args.add:
        parser.error("at least one --add NAME=PATH is required")
    payloads = []
    for item in args.add:
        name, _, path = item.partition("=")
        if not path:
            parser.error(f"--add {item!r} needs NAME=PATH")
        data = Path(path).read_bytes()
        payloads.append((name, data))
        print(f"{name}: {len(data)} bytes from {path}")
    minimum = args.size or (sum(len(data) for _, data in payloads) + 8 * 1024 * 1024)
    image = build(payloads, minimum)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(image)
    print(f"{args.out}: {len(image)} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
