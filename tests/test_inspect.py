"""Synthetic fixtures only: no vendor firmware or host hardware access."""

import hashlib
from pathlib import Path
import struct
import tempfile
import unittest

from tools.spark_fw import FMP_GUID, FormatError, inspect_image


class InspectionTests(unittest.TestCase):
    def inspect(self, data):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.dat"
            path.write_bytes(data)
            result = inspect_image(path)
            self.assertEqual(path.read_bytes(), data)
            return result

    def test_unknown_bytes_remain_unknown_without_leaking_local_directory(self):
        data = b"MT8901 is a label, not an instruction-set identification."
        result = self.inspect(data)
        self.assertEqual(result, {"name": "fixture.dat", "size_bytes": len(data),
                                 "sha256": hashlib.sha256(data).hexdigest(), "format": "unknown"})

    def test_empty_input_is_unknown(self):
        self.assertEqual(self.inspect(b"")["format"], "unknown")

    def test_elf32_and_elf64_both_byte_orders(self):
        for bits, machine, entry in ((32, 40, 0x81000000), (64, 183, 0x80000000)):
            for order in ("<", ">"):
                with self.subTest(bits=bits, byte_order=order):
                    header = bytearray(52 if bits == 32 else 64)
                    header[:7] = b"\x7fELF" + bytes([1 if bits == 32 else 2,
                                                   1 if order == "<" else 2, 1])
                    struct.pack_into(order + "HHI", header, 16, 2, machine, 1)
                    struct.pack_into(order + ("I" if bits == 32 else "Q"), header, 24, entry)
                    struct.pack_into(order + "H", header, 40 if bits == 32 else 52, len(header))
                    result = self.inspect(header)
                    self.assertEqual(result["bits"], bits)
                    self.assertEqual(result["entry_point"], hex(entry))
                    self.assertEqual(result["architecture"], "Arm" if bits == 32 else "AArch64")

    def test_bad_elf_is_rejected(self):
        for data in (b"\x7fELF", b"\x7fELF\x02\x01\x01" + bytes(57)):
            with self.subTest(length=len(data)), self.assertRaises(FormatError):
                self.inspect(data)

    def test_pe_aarch64_and_out_of_bounds_offsets(self):
        image = bytearray(88)
        image[:2] = b"MZ"
        struct.pack_into("<I", image, 60, 64)
        image[64:68] = b"PE\0\0"
        struct.pack_into("<H", image, 68, 0xAA64)
        self.assertEqual(self.inspect(image)["architecture"], "AArch64")
        struct.pack_into("<I", image, 60, 0xFFFFFFF0)
        with self.assertRaises(FormatError):
            self.inspect(image)

    def test_pe_missing_optional_header_or_sections_is_rejected(self):
        image = bytearray(88)
        image[:2] = b"MZ"
        struct.pack_into("<I", image, 60, 64)
        image[64:68] = b"PE\0\0"
        struct.pack_into("<HH", image, 68, 0xAA64, 1)
        with self.assertRaises(FormatError):
            self.inspect(image)

    def test_fmp_wrapper_and_inconsistent_sizes(self):
        image = FMP_GUID.bytes_le + struct.pack("<III", 28, 0, 36) + bytes(8)
        result = self.inspect(image)
        self.assertEqual(result["format"], "uefi-fmp-capsule")
        self.assertEqual(result["payload_size_bytes"], 8)
        self.assertNotIn("architecture", result)
        for bad in (image[:-1], image[:16] + struct.pack("<III", 40, 0, 36) + bytes(8)):
            with self.subTest(data=bad), self.assertRaises(FormatError):
                self.inspect(bad)

    def test_cab_wrapper_is_not_mistaken_for_an_executable(self):
        image = bytearray(36)
        image[:4] = b"MSCF"
        struct.pack_into("<I", image, 8, 36)
        result = self.inspect(image)
        self.assertEqual(result["format"], "microsoft-cabinet")
        self.assertNotIn("entry_point", result)
        with self.assertRaises(FormatError):
            self.inspect(image[:-1])


if __name__ == "__main__":
    unittest.main()
