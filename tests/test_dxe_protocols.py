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


class InspectionAfterHandoffTests(unittest.TestCase):
    """What a DXE snapshot can still prove once the payload may have taken the machine over.

    The DXE core's image is boot-services memory, so a run that reached a kernel can find the table
    gone without anything being wrong with the firmware. Measured over the stored runs: every run
    whose table was missing had reached userspace, and runs that reached userspace sometimes still
    found it.
    """

    verdict = {"table_address": "0x84000100", "protocols": []}

    def test_a_found_table_is_returned_unchanged(self):
        from tools.boot_spark import protocol_table_inspection
        result = protocol_table_inspection(b"no payload here", lambda: self.verdict)
        self.assertEqual(result, {"architectural_protocols": self.verdict})

    def test_a_missing_table_after_a_handoff_is_recorded_not_raised(self):
        from tools.boot_spark import protocol_table_inspection

        def missing():
            raise ValueError("expected one complete architectural protocol table; found 0")

        result = protocol_table_inspection(b"EFI stub: Booting Linux Kernel\n", missing)
        self.assertNotIn("architectural_protocols", result)
        self.assertIn("EFI stub:", result["architectural_protocols_source"])

    def test_a_missing_table_without_a_handoff_is_still_a_failure(self):
        from tools.boot_spark import protocol_table_inspection

        def missing():
            raise ValueError("expected one complete architectural protocol table; found 0")

        with self.assertRaises(ValueError):
            protocol_table_inspection(b"Checkpoint 61\nCheckpoint 96\n", missing)

    def test_two_tables_after_a_handoff_is_still_a_failure(self):
        """Only "found 0" is explained by the guest reusing the memory; two tables is our bug."""
        from tools.boot_spark import protocol_table_inspection

        def duplicate():
            raise ValueError("expected one complete architectural protocol table; found 2")

        with self.assertRaises(ValueError):
            protocol_table_inspection(b"Run /init as init process\n", duplicate)
