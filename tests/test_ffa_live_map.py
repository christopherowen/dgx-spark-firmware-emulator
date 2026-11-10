"""The live FF-A map, and how it lines up with the manifests we hold.

Everything here is read from artifacts: the manifests in the capsule, and the sysfs inventory recorded from a
unit. The point of the test is that the mapping is a *derivation* — a UUID that appears in both places has
to match under a stated byte order, and the fan service's identity has to fall out of the DSDT constants.
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

RECORD = ROOT / "research" / "ffa-partitions-live.json"
CAPSULE = ROOT / "firmware" / "prepared" / "2.158.15" / "socfw.cap"
FAN_HI, FAN_LO = 0x20418532A0634A88, 0x46A5A008C0EEAA83


def as_uuid(data: bytes) -> str:
    a, b, c = struct.unpack_from(">IHH", data, 0)
    return (f"{a:08x}-{b:04x}-{c:04x}-{data[8:10].hex()}-{data[10:16].hex()}")


class LiveMapTests(unittest.TestCase):
    def setUp(self):
        self.record = json.loads(RECORD.read_text())

    def test_the_fan_constants_are_the_fan_partition_uuid(self):
        """x2/x3 of SMC 0xC400008D are one UUID, not two opaque constants."""
        raw = struct.pack("<QQ", FAN_HI, FAN_LO)
        self.assertEqual(as_uuid(raw), "884a63a0-3285-4120-83aa-eec008a0a546")
        self.assertIn("884a63a0-3285-4120-83aa-eec008a0a546", self.record["partitions"]["0x8003"])

    def test_the_flash_uuid_property_is_two_uuids(self):
        entry = self.record["identifications"]["0x8002 / 3cbb7dc8-f848-bc47-222b-9da596629a96 and c87dbb3c-47bc-48f8-a59d-2b22969a6296"]
        self.assertIn("two", entry)

    @unittest.skipUnless(CAPSULE.is_file(), "the prepared capsule is not present")
    def test_the_manifest_uuids_appear_in_the_live_map(self):
        payload = capsule_payload(CAPSULE.read_bytes())

        def manifest_bytes(name):
            data = container.extract(payload, name)
            body = data[container.body_offset(data):]
            package = spkg.package(body)
            manifest = spkg.manifest(body[package.manifest_offset:package.manifest_offset + package.manifest_length])
            at, length = manifest.measured["uuid"]
            raw = body[package.manifest_offset:package.manifest_offset + package.manifest_length][at:at + length]
            return raw

        flash = manifest_bytes("FLASH")
        self.assertEqual(len(flash), 32, "the Flash Service publishes two UUIDs")
        live = set(self.record["partitions"]["0x8002"])
        for index in (0, 16):
            self.assertIn(as_uuid(flash[index:index + 16]), live,
                          "each half is a partition UUID, in canonical order")

        stmm = manifest_bytes("STMM")
        self.assertEqual(len(stmm), 16)
        words_reversed = b"".join(stmm[at:at + 4][::-1] for at in range(0, 16, 4))
        self.assertIn(as_uuid(words_reversed), self.record["partitions"]["0x8004"],
                      "STMM's manifest stores its UUID as four little-endian words; reversing each word "
                      "recovers the live value")
        self.assertNotIn(as_uuid(stmm), self.record["partitions"]["0x8004"],
                         "and the canonical decode does not — that is the vendor's inconsistency")


if __name__ == "__main__":
    unittest.main()
