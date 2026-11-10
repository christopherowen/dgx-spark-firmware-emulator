"""Store editing: the shape that broke the board, and the edits that undo it."""

import struct
import tempfile
import unittest
from pathlib import Path

from tools.nor_edit import (INDEX_NAME, VALID, dmi_entries, find, poison_findings,
                            set_dmi_entries, variables)

SIZE = 0x2000000
BANK = 0x40000


from tests.store_fixtures import entry as _entry  # the shared builder
from tests.store_fixtures import image as _image


def variable(name: str, data: bytes, attributes: int = 0x83, index: int = 0x0C) -> bytes:
    # This suite exercises the editor, which keys off a non-zero index byte; the shared builder
    # defaults to zero, so the difference is stated here rather than lost in a copy.
    return _entry(name, data, attributes=attributes, index=index)


def image(entries: list[bytes]) -> bytes:
    return _image(entries)


def blank_index(count: int = 0) -> bytes:
    return variable(INDEX_NAME, bytes([count]) + b"\x00" * (0x500 - 1))


class NorEditTests(unittest.TestCase):
    def test_orphan_records_are_reported(self):
        data = image([blank_index(),
                      variable("DmiVar0B42420084", b"\x00" * 8),
                      variable("DmiVar0B0B000084", b"\x00" * 8)])
        self.assertEqual(dmi_entries(data), [])
        records = [v.name for v in variables(data) if v.valid and v.name.startswith("DmiVar")]
        self.assertEqual(len(records), 2)

    def test_populating_the_index_matches_the_record_names(self):
        data = bytearray(image([blank_index(),
                                variable("DmiVar0B42420084", b"\x00" * 8),
                                variable("DmiVar0B0B000084", b"\x00" * 8)]))
        set_dmi_entries(data, ["0B42420084", "0B0B000084"])
        self.assertEqual(dmi_entries(bytes(data)), ["0B42420084", "0B0B000084"])
        index = find(bytes(data), INDEX_NAME)
        self.assertEqual(data[index.payload], 2)

    def test_entries_before_the_count_are_left_alone(self):
        data = bytearray(image([blank_index()]))
        index = find(bytes(data), INDEX_NAME)
        before = bytes(data[index.offset:index.payload])
        set_dmi_entries(data, ["0B42420084"])
        self.assertEqual(bytes(data[index.offset:index.payload]), before)

    def test_tombstone_clears_only_the_valid_bit(self):
        data = bytearray(image([blank_index()]))
        target = find(bytes(data), INDEX_NAME)
        data[target.offset + 9] = target.attributes & ~VALID
        self.assertEqual(data[target.offset + 9], 0x03)
        self.assertEqual(find(bytes(data), INDEX_NAME).attributes, 0x03)
        self.assertFalse(find(bytes(data), INDEX_NAME).valid)

    def test_wrong_entry_length_is_refused(self):
        data = bytearray(image([blank_index()]))
        with self.assertRaisesRegex(ValueError, "not a 5-byte entry"):
            set_dmi_entries(data, ["0B4242"])

    def test_too_many_entries_are_refused(self):
        data = bytearray(image([blank_index()]))
        with self.assertRaisesRegex(ValueError, "at most 255"):
            set_dmi_entries(data, ["0000000000"] * 256)

    def test_poison_check_reports_a_short_declaration(self):
        # the firing shape: 128 stored bytes, a record that declares 5
        record = bytes([0x0B, 5, 0x0B, 0x00]) + b"\x00" * 124
        data = image([blank_index(), variable("DmiVar0B0B000084", record)])
        findings = poison_findings(data)
        self.assertEqual(len(findings), 1, findings)
        self.assertIn("slack 123", findings[0])
        self.assertIn("zero run 121", findings[0])

    def test_poison_check_is_quiet_within_two_bytes_of_slack(self):
        # slack 2 has been measured to boot, so it is not reported
        record = bytes([0x0B, 126, 0x0B, 0x00]) + b"\x00" * 124
        data = image([blank_index(), variable("DmiVar0B0B000084", record)])
        self.assertEqual(poison_findings(data), [])

    def test_poison_check_ignores_invalid_records(self):
        record = bytes([0x0B, 5, 0x0B, 0x00]) + b"\x00" * 124
        data = image([blank_index(),
                      variable("DmiVar0B0B000084", record, attributes=0x03)])
        self.assertEqual(poison_findings(data), [])

    def test_missing_variable_names_the_variable(self):
        with self.assertRaisesRegex(KeyError, "DmiArray"):
            find(image([]), INDEX_NAME)


if __name__ == "__main__":
    unittest.main()
