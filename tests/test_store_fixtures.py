"""The store fixtures must be the format the parsers and the firmware see.

These tests are fast and need no vendor bytes. They exist because the fixture
generator is the only way a clean clone can exercise store parsing at all, and a
generator that drifts from the measured format would make every downstream test a
test of the generator instead.
"""

import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.store_fixtures import BANK, PART_SIZE, dmi_index, entry, image  # noqa: E402
from tools.nor_edit import INDEX_NAME, poison_findings, variables  # noqa: E402
from tools import nor_image  # noqa: E402


class FixtureFormatTests(unittest.TestCase):
    def test_header_matches_the_measured_vendor_shape(self):
        # Measured on a readback: erased up to 0x20, the volume length, `_FVH`, an erased
        # attribute word and a header length of 0x48. No checksum, because the store's own
        # volume carries none — a fixture with one would be stricter than the format.
        data = image([entry("Setup", b"\x01")])
        self.assertEqual(data[0x00:0x20], b"\xff" * 0x20)
        self.assertEqual(struct.unpack_from("<Q", data, 0x20)[0], BANK)
        self.assertEqual(data[0x28:0x2C], b"_FVH")
        self.assertEqual(data[0x2C:0x30], b"\xff" * 4)
        self.assertEqual(struct.unpack_from("<H", data, 0x30)[0], 0x48)

    def test_records_parse_in_both_banks_with_their_payloads(self):
        data = image([dmi_index(0), entry("Setup", b"\x02")], [entry("BootOrder", b"\x03")])
        first = variables(data, 0)
        second = variables(data, 1)
        self.assertEqual([v.name for v in first], [INDEX_NAME, "Setup"])
        self.assertEqual([v.name for v in second], ["BootOrder"])
        self.assertEqual(data[first[1].offset + first[1].size - 1:first[1].offset + first[1].size],
                         b"\x02")
        self.assertTrue(all(v.size > 10 for v in first + second))

    def test_a_clean_fixture_has_no_poison_findings(self):
        data = image([dmi_index(0), entry("Setup", b"\x01"), entry("BootOrder", b"\x02")])
        self.assertEqual(poison_findings(data), [])

    def test_the_volume_is_visible_to_the_image_inspector(self):
        data = image([dmi_index(0), entry("Setup", b"\x01")])
        import tempfile

        handle = tempfile.NamedTemporaryFile(delete=False, suffix=".bin")
        handle.write(data)
        handle.close()
        self.addCleanup(Path(handle.name).unlink, missing_ok=True)
        report = nor_image.inspect(Path(handle.name))
        self.assertEqual(report.entries, 2)
        self.assertEqual(report.size, len(data))

    def test_the_part_size_form_is_accepted_and_the_rest_is_refused(self):
        self.assertEqual(len(image(size=PART_SIZE)), PART_SIZE)
        with self.assertRaises(ValueError):
            image(size=0x100000)


if __name__ == "__main__":
    unittest.main()
