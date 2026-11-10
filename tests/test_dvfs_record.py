"""What the SoC's DVFS components actually contain, checked against the artifact.

`research/dvfs.json` records that the DVFS implementation is not statically readable: the blocks
that arbitrate clocks and power ship as high-entropy payloads. The GPU partition was the last hope
for a readable operating-point table, and it carries signed-container tags over high-entropy bytes
too. This test keeps those two claims attached to the artifact they were measured on.
"""

import collections
import json
import math
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / "research" / "dvfs.json"
CAPSULE = ROOT / "firmware" / "prepared" / "2.158.15" / "socfw.cap"


class DvfsRecordTests(unittest.TestCase):
    def setUp(self):
        self.record = json.loads(RECORD.read_text())

    def test_the_record_names_the_image_it_measured(self):
        self.assertIn("image_sha256", self.record)
        self.assertTrue(self.record.get("finding"), "the record states what was found")

    def test_the_measured_components_are_high_entropy(self):
        """Corrected slice: the eight measured components are 7.9-8.0 — data, not a readable table.

        The record used to say CPUEB was sparse (1.76) and SSPM readable-ish (6.91); both were read
        from the wrong bytes, 0x203000 before where the image header puts the partition area. The
        claim is about these eight only: the record must not speak for the whole image, and high
        entropy does not distinguish encryption from compression — the UEFI component is 8.00 too and
        its LZMA stream decodes.
        """
        self.assertEqual(len(self.record["components"]), 8)
        for name, component in self.record["components"].items():
            self.assertGreater(component["entropy"], 7.5,
                               f"{name} is readable, so the negative finding is stale")

    def test_the_container_tags_are_where_they_are(self):
        """Corrected slice: the GPU partition holds no readable tag at all; PLL holds one."""
        gpu = self.record["components"].get("GPU")
        self.assertEqual(gpu["signed_containers"], [],
                         "no container tag survives in the corrected GPU slice")
        self.assertIn("PLL_CLK-1.0.0", self.record["components"]["PLL"]["signed_containers"])

    @unittest.skipUnless(CAPSULE.is_file(), "the prepared capsule is not present")
    def test_the_measurements_match_the_capsule(self):
        import sys
        sys.path.insert(0, str(ROOT))
        from tools import container

        import hashlib

        from tools.spark_image import capsule_payload
        payload = capsule_payload(CAPSULE.read_bytes())     # the slot image the offsets are in
        self.assertEqual(hashlib.sha256(CAPSULE.read_bytes()).hexdigest(),
                         self.record["source"]["capsule_sha256"], "the record names its own artifact")
        self.assertEqual(hashlib.sha256(payload).hexdigest(), self.record["source"]["slot_sha256"])
        tag = re.compile(rb"[A-Z][A-Z0-9_]{2,}-\d+\.\d+\.\d+")
        for name in ("GPU", "PLL", "SSPM"):
            data = container.extract(payload, name)
            counts = collections.Counter(data)
            entropy = -sum((count / len(data)) * math.log2(count / len(data))
                           for count in counts.values())
            recorded = self.record["components"][name]
            self.assertEqual(len(data), recorded["size"], f"{name} size")
            self.assertEqual(list(container.resolve(payload, name)), recorded["range"],
                             f"{name} resolved range")
            self.assertEqual(recorded["non_ff"], sum(1 for byte in data if byte != 0xFF))
            self.assertAlmostEqual(entropy, recorded["entropy"], delta=0.02, msg=f"{name} entropy")
            if "signed_containers" in recorded:
                self.assertEqual(sorted({match.group(0).decode() for match in tag.finditer(data)}),
                                 recorded["signed_containers"], f"{name} tags")


if __name__ == "__main__":
    unittest.main()
