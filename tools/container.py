"""The vendor capsule's component table: parse it, and extract a named component.

The SF_BOOT payload is not opaque. After the magic it carries a MediaTek image header
(`AAAA`, version, size) followed by a table of components, each introduced by `BBBB`, a
count, an eight-byte name, then size, offset and the offset of the next component. The
components are contiguous and ascending, which is what makes the table trustworthy: a
parse that produced an overlap or a backwards step would be wrong.

This module reads that table and hands back the bytes of one component. It is the only
way this repository can reach anything the firmware does *below* the UEFI image — the
secure monitor, the secure payload, the power-management firmware — without committing
vendor bytes: the caller passes the capsule it already downloaded.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

# The table's offsets are relative to where the MediaTek image header sits inside the slot image, not
# to the payload's start. In the 2.158.15 slot that position is 0x203000, which is why the partition
# area appears to start there; the position is *derived* from the header, never assumed, because a
# release whose header moves would otherwise be sliced at the wrong bytes — which is how BL31 once
# looked erased (it is 98.4% populated).

ENTRY_MAGIC = b"BBBB"
HEADER_MAGIC = b"AAAA"
ENTRY_SIZE = 32
NAME_SIZE = 8
MAX_ENTRIES = 64


class ContainerError(ValueError):
    """The payload does not carry a component table this module understands."""


@dataclass(frozen=True)
class Component:
    name: str
    size: int
    offset: int
    next_offset: int
    kind: int = 0

    @property
    def end(self) -> int:
        return self.offset + self.size


def table(payload: bytes) -> list[Component]:
    """Every component the payload's table names, in table order.

    The header is found by its magic rather than a fixed offset: the FMP/MSS1 wrappers in
    front of the SF_BOOT image vary between releases.
    """
    header = header_position(payload)          # the declared base, or the single magic match
    start = header + 0x10                      # the table follows the header, not somewhere later
    if payload[start:start + 8] != ENTRY_MAGIC + b"\x07\x00\x00\x00":
        raise ContainerError(f"the image header at {header:#x} is not followed by a component table")
    components: list[Component] = []
    cursor = start
    while len(components) < MAX_ENTRIES:
        if payload[cursor:cursor + 4] != ENTRY_MAGIC:
            break
        count, = struct.unpack_from("<I", payload, cursor + 4)
        if count != 7:
            raise ContainerError(f"unexpected entry marker at {cursor:#x}")
        name = payload[cursor + 8:cursor + 16].split(b"\x00")[0].decode("latin-1")
        size, offset, next_offset, kind = struct.unpack_from("<IIII", payload, cursor + 16)
        if not name or size == 0 or kind > 0xF:
            raise ContainerError(f"malformed entry for {name!r} at {cursor:#x}")
        # Components are laid out in ascending order with a gap allowed for alignment, so the
        # check is "the next one starts at or after this one ends", not equality.
        if next_offset < offset + size:
            raise ContainerError(f"{name}: next offset {next_offset:#x} overlaps its own end "
                                 f"{offset + size:#x}")
        components.append(Component(name, size, offset, next_offset, kind))
        cursor += ENTRY_SIZE
    if not components:
        raise ContainerError("the component table is empty")
    for earlier, later in zip(components, components[1:]):
        if later.offset < earlier.offset:
            raise ContainerError(f"{later.name} starts before {earlier.name}")
    for earlier, later in zip(components, components[1:]):
        if later.offset < earlier.end:
            raise ContainerError(f"{later.name} starts at {later.offset:#x}, inside "
                                 f"{earlier.name} [.., {earlier.end:#x})")
    names = [component.name for component in components]
    if len(set(names)) != len(names):
        raise ContainerError("two components share a name, so a name does not identify one range")
    return components


BRLAYOUT = (0x200, b"BRLYT\x00\x00\x00", 0x210)
SUBRECORD = b"\xde\xad\xbe\xef\xde\xad\xbe\xef"
SUBRECORD_SIZE = 0x20
SUBHEADER_LENGTH = 0xD0


def declared_header(payload: bytes) -> int | None:
    """The header offset the slot image states for itself, if it states one.

    A slot image carries `BRLYT` at 0x200 and a 32-bit pointer at 0x210. In every capsule and part
    image checked that pointer is exactly where the `AAAA` header sits — the vendor's own statement of
    the base, which is better evidence than a magic scan because it is not a guess.
    """
    offset, magic, pointer = BRLAYOUT
    if payload[offset:offset + len(magic)] != magic:
        return None
    return struct.unpack_from("<I", payload, pointer)[0]


def body_offset(data: bytes) -> int | None:
    """Where a wrapped component's payload begins, per its own descriptor chain — None if unwrapped.

    Callers must ask for this rather than hard-code 0x2b0: the offset is a property of the descriptor
    table, and a component with a different count or header length puts its payload elsewhere.
    """
    if not data or data[0] != 0x59 or len(data) < 3:
        return None
    cbor_end = 3 + struct.unpack_from(">H", data, 1)[0]
    if cbor_end + 0x10 > len(data):
        return None
    version, count = struct.unpack_from("<II", data, cbor_end)
    if data[cbor_end + 8:cbor_end + 16] != SUBRECORD or version != 1 or not 1 <= count <= 16:
        return None
    last = cbor_end + 0x10 + (count - 1) * SUBRECORD_SIZE
    if last + SUBRECORD_SIZE > len(data):
        return None
    _, _, sub_offset, header_length, _ = struct.unpack_from("<5I", data, last)
    offset = cbor_end + sub_offset + header_length
    return offset if offset <= len(data) else None


def self_described_extent(data: bytes) -> int | None:
    """How long a component says it is, from its own length chain — or None if it does not say.

    Each CBOR-wrapped component opens with a descriptor table: version, count, a
    `deadbeefdeadbeef` marker, then `count` 0x20-byte sub-records. The last sub-record states the
    sub-image's offset, its header length and its body length, and those three plus the CBOR header
    add up to the component's extent to the byte. That is a length check the component makes about
    itself, so it does not depend on the map being right.
    """
    if not data or data[0] != 0x59 or len(data) < 3:
        return None
    cbor_end = 3 + struct.unpack_from(">H", data, 1)[0]
    if cbor_end + 0x10 > len(data):
        return None
    version, count = struct.unpack_from("<II", data, cbor_end)
    if data[cbor_end + 8:cbor_end + 16] != SUBRECORD or version != 1 or not 1 <= count <= 16:
        return None
    last = cbor_end + 0x10 + (count - 1) * SUBRECORD_SIZE
    if last + SUBRECORD_SIZE > len(data):
        return None
    _, _, sub_offset, header_length, body_length, *_ = struct.unpack_from("<8I", data, last)
    if header_length != SUBHEADER_LENGTH:
        return None
    return cbor_end + sub_offset + header_length + body_length


def header_positions(payload: bytes) -> list[int]:
    """Every place the MediaTek image header appears; more than one means the choice is the caller's."""
    magic = HEADER_MAGIC + b"\x01\x00\x00\x00"
    found, cursor = [], payload.find(magic)
    while cursor >= 0:
        found.append(cursor)
        cursor = payload.find(magic, cursor + 1)
    return found


def header_position(payload: bytes) -> int:
    """Where the MediaTek image header sits in the slot image — the base the offsets are relative to.

    Ambiguity is an error rather than a first-match: a payload with two headers has two coordinate
    systems and this module has no way to know which one a name refers to.
    """
    declared = declared_header(payload)
    found = header_positions(payload)
    if declared is not None:
        if declared not in found:
            raise ContainerError(f"the image declares its header at {declared:#x} but no header is there")
        return declared
    if not found:
        raise ContainerError("no MediaTek image header in this payload")
    if len(found) > 1:
        raise ContainerError(f"{len(found)} image headers at {[hex(at) for at in found]}; "
                             f"the base is ambiguous")
    return found[0]


def resolve(payload: bytes, name: str, base: int | None = None) -> tuple[int, int]:
    """The half-open payload range one component occupies, in the payload's own coordinates.

    Records pin this rather than a bare offset, so a reader can tell which coordinate space a
    measurement was taken in — the mistake that produced the "erased" components.
    """
    if base is None:
        base = header_position(payload)
    if base < 0:
        raise ContainerError(f"base {base:#x} is negative")
    for component in table(payload):
        if component.name == name:
            start = base + component.offset
            end = start + component.size
            if end > len(payload):
                raise ContainerError(f"{name} runs past the payload: {component.size:#x} at "
                                     f"{component.offset:#x} (+{base:#x})")
            return start, end
    raise ContainerError(f"no component named {name!r}")


def extract(payload: bytes, name: str, base: int | None = None) -> bytes:
    """The bytes of one named component, checked against the table's own bounds.

    `base` defaults to the header's own position, which is the base the table's offsets are relative
    to. Passing 0 reproduces the old, wrong slice; it exists only so a historical measurement can be
    compared against what it actually read.
    """
    start, end = resolve(payload, name, base)
    return payload[start:end]
