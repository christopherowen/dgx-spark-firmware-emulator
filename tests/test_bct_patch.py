"""The capsule's `BCT` is a patch of four switches, and the page that said "erased" was corrected.

`research/bct-patch.json` records what the 2.158.15 capsule carries at the coordinates the map really
uses: base 0x203000 plus the component offset, so `BCT` sits at 0x1b95000 and is 24.9% filled. Read at
the raw map offset it looks empty, which is how the first version of `docs/ram-timings.md` concluded
"erased in every artifact". This test re-derives the facts from the capsule so the correction cannot
drift back.
"""

import hashlib
import json
import struct
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / "research" / "bct-patch.json"
CAPSULE = ROOT / "firmware" / "prepared" / "2.158.15" / "socfw.cap"


class BctPatchTests(unittest.TestCase):
    def setUp(self):
        self.record = json.loads(RECORD.read_text())

    def test_the_record_states_the_correction_and_its_coordinates(self):
        self.assertEqual(self.record["map_base"], 0x203000)
        self.assertIn("0x203000", self.record["coordinate_note"])
        self.assertEqual(self.record["bct"]["slot_offset"], 0x1b95000)
        self.assertIn("patch", self.record["what_this_is"])

    def test_the_four_switches_are_the_ones_the_capsule_holds(self):
        names = [entry["name"] for entry in self.record["bct"]["records"]]
        self.assertEqual(names, ["DRAM_ENCRYPT", "TPM", "PLUTON", "PLUTON_DRTM"])
        for entry in self.record["bct"]["records"]:
            self.assertEqual(len(entry["values"]), 4, "each record carries four words")
        values = {entry["name"]: entry["values"] for entry in self.record["bct"]["records"]}
        self.assertEqual(values["DRAM_ENCRYPT"], [0, 0, 0, 0], "encryption off in this release")

    def test_no_record_is_a_timing(self):
        text = json.dumps(self.record["bct"]["records"]).lower()
        for word in ("trfc", "tras", "trcd", "frequency", "voltage"):
            self.assertNotIn(word, text)

    @unittest.skipUnless(CAPSULE.is_file(), "the prepared capsule is not present")
    def test_the_capsule_still_matches_the_record(self):
        capsule = CAPSULE.read_bytes()
        self.assertEqual(hashlib.sha256(capsule).hexdigest(), self.record["capsule_sha256"])
        from tools.spark_image import capsule_payload

        slot = capsule_payload(capsule)          # the coordinates the record is written in
        offset = self.record["bct"]["slot_offset"]
        size = self.record["bct"]["size"]
        region = slot[offset:offset + size]
        self.assertEqual(sum(1 for byte in region if byte != 0xFF),
                         self.record["bct"]["non_ff_bytes"])
        magic = region.find(b"BCT\x00")
        self.assertEqual(magic, self.record["bct"]["header"]["magic_at"])
        self.assertEqual(struct.unpack_from("<I", region, magic + 4)[0],
                         self.record["bct"]["header"]["version"])
        self.assertEqual(region[magic + 8:magic + 40].hex(), self.record["bct"]["header"]["sha256"])
        # every record sits where the record says, and its name is where the name says
        for record in self.record["bct"]["records"]:
            self.assertEqual(region[record["record_at"]:record["record_at"] + 4], b"BCTV",
                             f"{record['name']} record header")
            self.assertEqual(region[record["name_at"]:region.find(b"\x00", record["name_at"])],
                             record["name"].encode())
            values = struct.unpack_from("<4I", region, record["name_at"] + 12)
            self.assertEqual(list(values), record["values"], f"{record['name']} values")

        # and the neighbouring partitions really are empty — every byte, not a prefix: a page that
        # reports "carries nothing" must have looked at all of it.
        for name in ("ddrfsk", "misc"):
            start = self.record[name]["slot_offset"]
            size = self.record[name]["size"]
            non_ff = sum(1 for byte in slot[start:start + size] if byte != 0xFF)
            self.assertEqual(non_ff, 0, f"{name} carries nothing over its whole {size:#x} bytes")


if __name__ == "__main__":
    unittest.main()
