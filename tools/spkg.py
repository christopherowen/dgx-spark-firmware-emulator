"""The `SPKG` containers the capsule carries, and the FF-A manifests inside them.

`STMM`, `FLASH` and `ESPI` are not blobs with a magic: each is a signed-package header followed by an
Arm FF-A partition manifest (a flattened device tree) and then the partition's image. The manifest
states what the partition is for — `description`, `uuid`, `id`, load address, messaging method, memory
regions, device regions with base addresses, interrupts and SMMU stream ids.

That matters beyond bookkeeping: the `ESPI` manifest's partition id is `0x8003`, which is the endpoint
the ACPI fan methods call, so the fan path and this manifest describe the same component.

Everything here is a bounded read of a declared structure. No scanning for patterns, no guessing a
field's meaning from its neighbours: a field that is not understood is reported by offset and length.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

SPKG_MAGIC = b"SPKG"
SUPPORTED_VERSION = 2
FDT_MAGIC = 0xD00DFEED

# SPKG v2 header: magic, version, then three (offset, length) pairs. The third pair is the image that
# follows the manifest; the first two are the manifest (device tree) and a reserved area.
HEADER_SIZE = 0x18
FDT_BEGIN_NODE, FDT_END_NODE, FDT_PROP, FDT_NOP, FDT_END = 1, 2, 3, 4, 9


class SpkgError(ValueError):
    """The container or its manifest is not the structure this module understands."""


@dataclass(frozen=True)
class Package:
    version: int
    manifest_offset: int
    manifest_length: int
    image_offset: int
    image_length: int

    def extents(self) -> dict[str, tuple[int, int]]:
        return {"manifest": (self.manifest_offset, self.manifest_length),
                "image": (self.image_offset, self.image_length)}


@dataclass
class Manifest:
    properties: dict[str, object] = field(default_factory=dict)
    regions: list[dict[str, object]] = field(default_factory=list)
    measured: dict[str, tuple[int, int]] = field(default_factory=dict)


def package(data: bytes) -> Package:
    """Read the SPKG header of one component body."""
    if data[:4] != SPKG_MAGIC:
        raise SpkgError(f"expected SPKG, found {data[:4]!r}")
    version, = struct.unpack_from("<I", data, 4)
    if version != SUPPORTED_VERSION:
        raise SpkgError(f"SPKG version {version} is not the one this module reads")
    (mdt_off, mdt_len), (img_off, img_len), (_reserved) = (
        struct.unpack_from("<II", data, 8),
        struct.unpack_from("<II", data, 0x10),
        struct.unpack_from("<II", data, 0x18) if len(data) >= 0x20 else (0, 0))
    if mdt_off + mdt_len > len(data) or img_off + img_len > len(data):
        raise SpkgError("an SPKG extent runs past the component")
    if mdt_len == 0 or img_len == 0:
        raise SpkgError("an SPKG extent is empty")
    return Package(version, mdt_off, mdt_len, img_off, img_len)


def _align4(value: int) -> int:
    return (value + 3) & ~3


TEXT_PROPERTIES = frozenset({"compatible", "description", "name", "dev-name"})


def _value(data: bytes, start: int, length: int, name: str = ""):
    raw = data[start:start + length]
    # An address cell is not a string: 0x50000000 is four bytes that happen to be printable. Only the
    # properties that the manifest defines as text are decoded as text, and everything else keeps its
    # numeric value, so a record cannot quote "P" where the firmware means 0x50000000.
    if name in TEXT_PROPERTIES:
        return raw.rstrip(b"\x00").decode(errors="replace")
    if length in (4, 8):
        return int.from_bytes(raw, "big")
    if all(32 <= byte < 127 or byte == 0 for byte in raw) and raw.endswith(b"\x00"):
        return raw.rstrip(b"\x00").decode()
    if all(32 <= byte < 127 or byte == 0 for byte in raw):
        return raw.rstrip(b"\x00").decode()
    words = [int.from_bytes(raw[index:index + 4], "big")
             for index in range(0, length - length % 4, 4)]
    return words if words else raw.hex()


def manifest(dtb: bytes) -> Manifest:
    """Walk the manifest tree: properties per node, with nothing inferred."""
    if len(dtb) < 40:
        raise SpkgError("manifest is too short to be a device tree")
    magic, total, struct_at, strings_at, _mem, _ver, _last, _cpu, size_strings, _size_struct = \
        struct.unpack_from(">10I", dtb, 0)
    if magic != FDT_MAGIC:
        raise SpkgError(f"manifest magic {magic:#x} is not a device tree")
    if total > len(dtb):
        raise SpkgError("the manifest declares more bytes than it has")
    if strings_at + size_strings > total:
        raise SpkgError("the manifest's string block runs past the tree")
    result = Manifest()
    path: list[str] = []
    depth = 0
    cursor = struct_at
    while cursor + 4 <= total:
        token, = struct.unpack_from(">I", dtb, cursor)
        cursor += 4
        if token == FDT_BEGIN_NODE:
            end = dtb.index(b"\x00", cursor)
            name = dtb[cursor:end].decode() or "/"
            cursor = _align4(end + 1)
            path = path[:depth] + [name]
            depth += 1
        elif token == FDT_END_NODE:
            depth -= 1
            path = path[:depth]
        elif token == FDT_PROP:
            length, name_offset = struct.unpack_from(">II", dtb, cursor)
            cursor += 8
            name_end = dtb.index(b"\x00", strings_at + name_offset)
            name = dtb[strings_at + name_offset:name_end].decode()
            value = _value(dtb, cursor, length, name)
            node = "/".join(path)
            if node == "/":
                result.properties[name] = value
                result.measured[name] = (cursor, length)
            else:
                entry = next((item for item in result.regions
                              if item["node"] == node), None)
                if entry is None:
                    entry = {"node": node}
                    result.regions.append(entry)
                entry[name] = value
                entry.setdefault("measured", {})[name] = (cursor, length)
            cursor = _align4(cursor + length)
        elif token == FDT_NOP:
            continue
        elif token == FDT_END:
            break
        else:
            raise SpkgError(f"unexpected device-tree token {token:#x}")
    if not result.properties:
        raise SpkgError("the manifest names nothing")
    return result
