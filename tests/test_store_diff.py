"""Store comparison must work by variable name, not by offset."""

import unittest
from pathlib import Path
import tempfile

from tools.nor_edit import variables
from tools.store_diff import collect, first_difference


def variable(name: str, data: bytes, attributes: int = 0x83) -> bytes:
    body = bytes([0x0C]) + name.encode("ascii") + b"\x00" + data
    size = 10 + len(body)
    return b"NVAR" + size.to_bytes(2, "little") + b"\xff\xff\xff" + bytes([attributes]) + body


def image(entries: list[bytes]) -> bytes:
    data = bytearray(b"\xff" * 0x2000000)
    for base in (0x28, 0x40028):
        data[base:base + 4] = b"_FVH"
    cursor = 0x60
    for entry in entries:
        data[cursor:cursor + len(entry)] = entry
        cursor += len(entry)
    return bytes(data)


class StoreDiffTests(unittest.TestCase):
    def write(self, data: bytes) -> Path:
        handle = tempfile.NamedTemporaryFile(delete=False, suffix=".bin")
        handle.write(data)
        handle.close()
        self.addCleanup(Path(handle.name).unlink, missing_ok=True)
        return Path(handle.name)

    def test_same_variable_at_different_offsets_is_not_a_difference(self):
        left = collect(self.write(image([variable("Setup", b"\x01")])).read_bytes())
        right = collect(self.write(image([variable("BootOrder", b"\x02"),
                                          variable("Setup", b"\x01")])).read_bytes())
        self.assertEqual(left[(0, "Setup")][0][1], right[(0, "Setup")][0][1])
        self.assertNotEqual(left[(0, "Setup")][0][0].offset, right[(0, "Setup")][0][0].offset)

    def test_changed_payload_reports_first_differing_byte(self):
        left = collect(self.write(image([variable("DmiArray", b"\x00\x00\x00")])).read_bytes())
        right = collect(self.write(image([variable("DmiArray", b"\x00\x02\x00")])).read_bytes())
        # header 10 + index byte 1 + "DmiArray" 8 + NUL 1 = 0x14, so the second
        # payload byte is at +0x15
        self.assertEqual(first_difference(left[(0, "DmiArray")][0][1], right[(0, "DmiArray")][0][1]),
                         "first difference at +0x15: 0x00 vs 0x02")

    def test_missing_variable_is_named(self):
        left = collect(self.write(image([variable("Setup", b"\x01")])).read_bytes())
        right = collect(self.write(image([variable("BootOrder", b"\x02")])).read_bytes())
        self.assertEqual(set(left) - set(right), {(0, "Setup")})
        self.assertEqual(set(right) - set(left), {(0, "BootOrder")})

    def test_length_change_is_reported(self):
        self.assertEqual(first_difference(b"ab", b"abc"), "length differs: 2 vs 3")
        self.assertEqual(first_difference(b"ab", b"ab"), "identical")


if __name__ == "__main__":
    unittest.main()
