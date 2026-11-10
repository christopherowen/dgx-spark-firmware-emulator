"""The eSPI service's SPBM register map, re-derived from the image it is recorded from.

The service behind the ACPI fan methods also carries the power-limit and thermal code, and the register
identifiers it uses are built by `movz`/`movk` pairs in the image. This test extracts them again and
compares with the record, so the map cannot drift from the artifact.
"""

import json
import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import container, spkg  # noqa: E402
from tools.spark_image import capsule_payload  # noqa: E402

RECORD = ROOT / "research" / "espi-spbm-registers.json"
CAPSULE = ROOT / "firmware" / "prepared" / "2.158.15" / "socfw.cap"


def registers(image: bytes) -> dict:
    """SPBM ids built as `movz wD,#low ; movk wD,#0x600,lsl 16`."""
    found = {}
    for at in range(0, len(image) - 8, 4):
        first = struct.unpack_from("<I", image, at)[0]
        second = struct.unpack_from("<I", image, at + 4)[0]
        if first & 0x7F800000 == 0x52800000 and second & 0x7F800000 == 0x72800000:
            low = ((first >> 5) & 0xFFFF) << (((first >> 21) & 3) * 16)
            high = ((second >> 5) & 0xFFFF) << (((second >> 21) & 3) * 16)
            value = low | high
            if 0x06000000 <= value <= 0x0600FFFF:
                found.setdefault(value, []).append(at)
    return found


class EspiRegisterTests(unittest.TestCase):
    def setUp(self):
        self.record = json.loads(RECORD.read_text())

    def test_the_record_says_what_it_does_not_claim(self):
        """Documentation lint: a static register map does not establish normal-world permission."""
        self.assertEqual(self.record["verification"],
                         {"normal_world_read": "not_established",
                          "normal_world_write": "not_established",
                          "frequency_change": "not_established"})
        self.assertTrue(self.record["not_claimed"])

    def test_the_power_limit_block_is_part_of_the_map(self):
        for register in self.record["power_limit_block"]["registers"]:
            self.assertIn(register, self.record["other_registers"])

    @unittest.skipUnless(CAPSULE.is_file(), "the prepared capsule is not present")
    def test_the_map_matches_the_image(self):
        import hashlib

        payload = capsule_payload(CAPSULE.read_bytes())
        data = container.extract(payload, "ESPI")
        body = data[container.body_offset(data):]
        package = spkg.package(body)
        image = body[package.image_offset:]
        self.assertEqual(hashlib.sha256(image).hexdigest(), self.record["source"]["image_sha256"])
        found = registers(image)
        recorded = {int(key, 16): value for key, value in self.record["other_registers"].items()}
        self.assertEqual(sorted(found), sorted(recorded), "the register set")
        for value, sites in found.items():
            self.assertEqual([hex(at) for at in sites[:3]], recorded[value], f"0x{value:08x} sites")


if __name__ == "__main__":
    unittest.main()
