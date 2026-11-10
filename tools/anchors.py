"""Find the code that refers to a string in a raw AArch64 image, without knowing its load address.

`adrp` + `add` is how this code addresses its own data, and the pair resolves to a **file offset** that
does not depend on where the image is loaded:

    offset(target) = (at & ~0xFFF) + sign_extend(imm21) * 0x1000 + imm12

That is why this works on a bare image: every saved decompile of this component quotes addresses from
whichever build its author had, and transplanting them has already produced one wrong answer. What is
stable across builds is the *structure*, so the tool reports file offsets and the caller maps them.

Nothing here disassembles: it decodes two instruction forms and reports where they point.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

ADRP_MASK = 0x9F000000
ADRP_OPCODE = 0x90000000
ADD_MASK = 0xFF800000          # ADD (immediate), 64-bit, no shift
ADD_OPCODE = 0x91000000
# how many instructions to look ahead for the `add` that completes the pair
WINDOW = 4


@dataclass(frozen=True)
class Reference:
    at: int             # file offset of the adrp
    add_at: int         # file offset of the add
    register: int       # the destination register the add completes
    target: int         # file offset the pair addresses
    instruction: int = 0


def _sign_extend(value: int, bits: int) -> int:
    if value & (1 << (bits - 1)):
        return value - (1 << bits)
    return value


def instructions(image: bytes):
    """(offset, word) for every complete instruction in the image."""
    for at in range(0, len(image) - 3, 4):
        yield at, struct.unpack_from("<I", image, at)[0]


def adrp_pages(image: bytes):
    """(offset, destination register, page delta) for every adrp."""
    for at, word in instructions(image):
        if word & ADRP_MASK == ADRP_OPCODE:
            register = word & 0x1F
            immediate = ((word >> 5) & 0x7FFFF) << 2 | ((word >> 29) & 0x3)
            yield at, register, _sign_extend(immediate, 21)


def references(image: bytes):
    """Every adrp/add pair, resolved to the file offset it addresses."""
    pages = list(adrp_pages(image))
    by_register = {}
    for at, register, delta in pages:
        by_register.setdefault(register, []).append((at, delta))
    found = []
    for at, register, delta in pages:
        page = (at & ~0xFFF) + delta * 0x1000
        for ahead in range(4, WINDOW * 4 + 1, 4):
            spot = at + ahead
            if spot + 4 > len(image):
                break
            word = struct.unpack_from("<I", image, spot)[0]
            if word & ADD_MASK != ADD_OPCODE:
                continue
            source_register = (word >> 5) & 0x1F
            destination = word & 0x1F
            if source_register != register:
                continue
            immediate = (word >> 10) & 0xFFF
            shift = (word >> 22) & 1
            if shift:
                immediate <<= 12
            found.append(Reference(at, spot, destination, page + immediate, word))
            break
    return found


def callers_of(image: bytes, target: int) -> list[Reference]:
    """Pairs that address exactly this file offset."""
    return [reference for reference in references(image) if reference.target == target]


def strings(image: bytes, minimum: int = 6):
    """(offset, text) for the NUL-terminated printable strings in the image."""
    start = 0
    for index, byte in enumerate(image):
        if byte == 0 or not 32 <= byte < 127:
            if index - start >= minimum:
                text = image[start:index]
                if all(32 <= value < 127 for value in text):
                    yield start, text.decode("latin-1")
            start = index + 1
        elif byte == 0:
            start = index + 1
