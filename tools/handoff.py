"""Synthetic loader memory tags for the full boot and SEC/DXE diagnostic."""

import struct

TAG_SIZE = 0x1868
TAG_MAGIC = 0x88610005

BOOT_REGIONS = (
    ("MMIO", 0, 0x40000000, 0),
    ("MMAP_MONITOR", 0x80000000, 0x100000, 0),
    ("MMAP_TINY_UEFI", 0x80100000, 0x7f00000, 1),
    ("MMAP_FREE_BASE_1", 0x88000000, 0xb3e0000, 1),
    ("MMAP_SECURE", 0x933e0000, 0xe60000, 0),
    ("MMAP_FREE_BASE_2", 0x94240000, 0xbdc0000, 1),
    ("MMAP_NOR_BACKING", 0xa0000000, 0x100000, 0),
    ("MMAP_FREE_BASE_3", 0xa0100000, 0x1ff00000, 1),
)

DIAGNOSTIC_REGIONS = (
    ("MMIO", 0, 0x40000000, 0),
    ("MMAP_TINY_UEFI", 0x80000000, 0x8000000, 1),
    ("MMAP_FREE_BASE_1", 0x88000000, 0x38000000, 1),
)


def memory_tag(regions):
    # SEC copies the complete fixed-size tag plus its eight-byte terminator.
    result = bytearray(TAG_SIZE + 8)
    struct.pack_into("<II", result, 0, TAG_SIZE, TAG_MAGIC)
    struct.pack_into("<I", result, 0x78, len(regions))
    for index, (name, base, size, flags) in enumerate(regions):
        offset = 0x80 + 56 * index
        result[offset:offset + len(name)] = name.encode("ascii")
        struct.pack_into("<QQI", result, offset + 32, base, size, flags)
    return bytes(result)


def boot_arguments():
    """Reserve the original monitor, real MM, communication pages and NOR."""
    return memory_tag(BOOT_REGIONS)


def diagnostic_boot_arguments():
    """Use the original 1 GiB diagnostic layout without monitor/MM reservations."""
    return memory_tag(DIAGNOSTIC_REGIONS)
