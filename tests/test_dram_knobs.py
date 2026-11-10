"""The DRAM knobs the firmware knows about, and the absence of a way to reach them.

`research/dram-knobs.json` records a build-configuration vocabulary inside the vendor UEFI: NUL
separated knob names with their legal values, including four DRAM ones. It is a statement about what
the firmware *can* be built with, not an interface a running machine offers — the partitions that
would carry the resulting timings are erased in every release we hold. This test re-derives the table
from the image so the record cannot drift away from the artifact it describes.
"""

import hashlib
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / "research" / "dram-knobs.json"
UEFI = ROOT / "firmware" / "prepared" / "2.158.15" / "uefi.fd"


def parse(image, begin):
    entries, strings = [], []
    for piece in image[begin:begin + 1400].split(b"\x00"):
        if not piece:
            continue
        if not all(32 <= byte < 127 or byte in (9, 10, 13) for byte in piece):
            break
        text = piece.decode("ascii").strip()
        if text:
            strings.append(text)
    index = 0
    while index < len(strings):
        name = strings[index]
        values = None
        if index + 1 < len(strings) and re.match(r"^\d+\s*:", strings[index + 1]):
            values = strings[index + 1]
            index += 2
        else:
            index += 1
        if re.match(r"^[A-Z][A-Z0-9_]{1,}$", name) or name.startswith("GP"):
            entries.append({"name": name, "values": values})
    return entries


class DramKnobTests(unittest.TestCase):
    def setUp(self):
        self.record = json.loads(RECORD.read_text())

    def test_the_record_says_it_is_a_vocabulary_and_not_an_interface(self):
        self.assertIn("not a runtime interface", self.record["description"])
        names = [entry["name"] for entry in self.record["entries"]]
        for knob in ("DRAM_ENCRYPT", "DRAM_VOL_LEVEL", "DRAM_RAPIDK_DIS", "DRAM_EYESCAN"):
            self.assertIn(knob, names)

    def test_the_voltage_levels_are_recorded_as_the_firmware_states_them(self):
        by_name = {entry["name"]: entry["values"] for entry in self.record["entries"]}
        self.assertEqual(by_name["DRAM_VOL_LEVEL"],
                         "0:NV, 1:HV, 2:LV, 3:6P, 4:7P, 5:8P, 6:9P, 7:10P")
        self.assertEqual(by_name["DRAM_RAPIDK_DIS"], "0:on, 1:off")

    @unittest.skipUnless(UEFI.is_file(), "the prepared release is not present")
    def test_the_record_matches_the_image_it_names(self):
        image = UEFI.read_bytes()
        self.assertEqual(hashlib.sha256(image).hexdigest(), self.record["image_sha256"])
        self.assertEqual(parse(image, self.record["table_offset"]), self.record["entries"])


if __name__ == "__main__":
    unittest.main()
