"""Synthetic runtime tables; no firmware bytes or hardware captures."""

import struct
import unittest
from uuid import UUID

from tools.dxe_protocols import PROTOCOLS, inspect_protocols


class ArchitecturalProtocolTests(unittest.TestCase):
    def fixture(self):
        base, start = 0x100000, 0x100
        image = bytearray(0x1000)
        for index, (_, guid) in enumerate(PROTOCOLS):
            offset = 0x800 + index * 16
            image[offset:offset + 16] = UUID(guid).bytes_le
            struct.pack_into("<Q", image, start + index * 40, base + offset)
            image[start + index * 40 + 32] = index % 2
        return image, base, start

    def test_reports_present_flags_only_for_complete_table(self):
        image, base, start = self.fixture()
        result = inspect_protocols(image, base)
        self.assertEqual(result["table_address"], hex(base + start))
        self.assertEqual(result["protocols"], [
            {"name": name, "present": bool(index % 2)}
            for index, (name, _) in enumerate(PROTOCOLS)])

    def test_rejects_partial_wrong_guid_invalid_flag_and_duplicate_table(self):
        image, base, start = self.fixture()
        wrong_guid = bytearray(image)
        wrong_guid[0x800] ^= 1
        invalid_flag = bytearray(image)
        invalid_flag[start + 32] = 2
        unterminated = bytearray(image)
        unterminated[start + 40 * len(PROTOCOLS)] = 1
        duplicate = bytearray(image)
        table = image[start:start + 40 * len(PROTOCOLS) + 8]
        duplicate[0x400:0x400 + len(table)] = table
        for candidate in (image[:0x810], wrong_guid, invalid_flag, unterminated, duplicate):
            with self.subTest(size=len(candidate)), self.assertRaises(ValueError):
                inspect_protocols(candidate, base)
