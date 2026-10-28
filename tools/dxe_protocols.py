"""Inspect an EDK II architectural-protocol table in a paused local DXE image."""

import struct
from uuid import UUID

# Protocol identifiers are public EDK II interface constants (MdePkg.dec).
# Table layout: MdeModulePkg/Core/Dxe/DxeMain/DxeProtocolNotify.c, AArch64.
PROTOCOLS = [
    ("Security", "a46423e3-4617-49f1-b9ff-d1bfa9115839"),
    ("Cpu", "26baccb1-6f42-11d4-bce7-0080c73c8881"),
    ("Metronome", "26baccb2-6f42-11d4-bce7-0080c73c8881"),
    ("Timer", "26baccb3-6f42-11d4-bce7-0080c73c8881"),
    ("Bds", "665e3ff6-46cc-11d4-9a38-0090273fc14d"),
    ("WatchdogTimer", "665e3ff5-46cc-11d4-9a38-0090273fc14d"),
    ("Runtime", "b7dfb4e1-052f-449f-87be-9818fc91b733"),
    ("Variable", "1e5668e2-8481-11d4-bcf1-0080c73c8881"),
    ("VariableWrite", "6441f818-6362-4e44-b570-7dba31dd2453"),
    ("Capsule", "5053697e-2cbc-4819-90d9-0580deee5754"),
    ("MonotonicCounter", "1da97072-bddc-4b30-99f1-72a0b56fff2a"),
    ("Reset", "27cfac88-46cc-11d4-9a38-0090273fc14d"),
    ("RealTimeClock", "27cfac87-46cc-11d4-9a38-0090273fc14d"),
]


def inspect_protocols(image, base):
    """Require all 13 ordered GUID pointers and a null terminator; never guess."""
    guids = [UUID(guid).bytes_le for _, guid in PROTOCOLS]
    candidates = []
    for start in range(0, len(image) - 40 * len(guids) - 7, 8):
        records = []
        for index, (name, _) in enumerate(PROTOCOLS):
            offset = start + index * 40
            pointer = struct.unpack_from("<Q", image, offset)[0] - base
            present = image[offset + 32]
            if (not 0 <= pointer <= len(image) - 16 or
                    image[pointer:pointer + 16] != guids[index] or present not in (0, 1)):
                break
            records.append({"name": name, "present": bool(present)})
        if (len(records) == len(guids) and
                struct.unpack_from("<Q", image, start + 40 * len(guids))[0] == 0):
            candidates.append({"table_address": hex(base + start), "protocols": records})
    if len(candidates) != 1:
        raise ValueError(f"expected one complete architectural protocol table; found {len(candidates)}")
    return candidates[0]
