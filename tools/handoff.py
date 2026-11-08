"""Synthetic loader memory tags for the full boot and SEC/DXE diagnostic."""

import struct

TAG_SIZE = 0x1868
TAG_MAGIC = 0x88610005

NOR_SIZE = 0x2000000
# The handoff tells SEC where RAM ends, and every region below stops here. A boot therefore gives
# the firmware the same 1 GiB window (0x80000000..RAM_TOP) whatever -m allocates; RAM above this is
# not described to the guest.
RAM_TOP = 0xC0000000
RAM_BASE = 0x80000000

BOOT_REGIONS = (
    ("MMIO", 0, 0x40000000, 0),
    ("MMAP_MONITOR", 0x80000000, 0x100000, 0),
    ("MMAP_TINY_UEFI", 0x80100000, 0x7f00000, 1),
    ("MMAP_FREE_BASE_1", 0x88000000, 0xb3e0000, 1),
    ("MMAP_SECURE", 0x933e0000, 0xe60000, 0),
    ("MMAP_FREE_BASE_2", 0x94240000, 0xbdc0000, 1),
    ("MMAP_NOR_BACKING", 0xa0000000, 0x2000000, 0),
    ("MMAP_FREE_BASE_3", 0xa2000000, 0x1e000000, 1),
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


def boot_arguments(nor_size=NOR_SIZE, ram_top=RAM_TOP):
    """Reserve the original monitor, real MM, communication pages and NOR.

    The free region after the NOR grows and shrinks with `nor_size`, so a 64 MiB
    model keeps the same top of RAM as the 32 MiB one. `ram_top` defaults to the
    original handoff ceiling; a caller that allocated more RAM may raise it, and
    the last region then describes the extra memory instead of hiding it.
    """
    nor_base = 0xa0000000
    # Seed window, one state page, live window: the loader owns the first, QEMU restores it on every
    # reset, and the monitor serves flash from the third, which no loader touches.
    reserved = 2 * nor_size + 0x1000
    top = max(RAM_TOP, ram_top)
    regions = tuple(
        ("MMAP_NOR_BACKING", nor_base, reserved, 0) if name == "MMAP_NOR_BACKING"
        else ("MMAP_FREE_BASE_3", nor_base + reserved, top - (nor_base + reserved), 1)
        if name == "MMAP_FREE_BASE_3"
        else (name, base, size, flags)
        for (name, base, size, flags) in BOOT_REGIONS
    )
    return memory_tag(regions)


def diagnostic_boot_arguments():
    """Use the original 1 GiB diagnostic layout without monitor/MM reservations."""
    return memory_tag(DIAGNOSTIC_REGIONS)
