"""What the encrypted components' metadata says, and why it closes the offline route.

Each encrypted component is preceded by a 0xd0-byte header carrying a 16-byte label, a 12-byte value,
the ASCII tag `AAD`, a 16-byte value, `AAD` again, and a 32-byte value. Distinct per component, every
time. That is the shape of an AEAD construction, and it is why "just decrypt it" is not an available
move: no nonce reuse, no unauthenticated oracle, no standard compressor underneath.
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import container  # noqa: E402
from tools.spark_image import capsule_payload  # noqa: E402

RECORD = ROOT / "research" / "component-crypto.json"
CAPSULE = ROOT / "firmware" / "prepared" / "2.158.15" / "socfw.cap"


class CryptoMetadataTests(unittest.TestCase):
    def setUp(self):
        self.record = json.loads(RECORD.read_text())

    def test_every_metadata_block_has_its_own_material(self):
        entries = self.record["components"]
        self.assertGreaterEqual(len(entries), 15)
        for field in ("nonce", "tag", "digest_32"):
            values = [entry[field] for entry in entries]
            self.assertEqual(len(set(values)), len(values),
                             f"{field} repeats — that would be a real weakness and this record would say so")

    def test_the_record_does_not_claim_a_cipher(self):
        text = json.dumps(self.record).lower()
        self.assertIn("inference", text, "the cipher is an inference and must be marked as one")
        self.assertIn("what_is_not_claimed", self.record)

    @unittest.skipUnless(CAPSULE.is_file(), "the prepared capsule is not present")
    def test_the_metadata_matches_the_capsule_field_by_field(self):
        import struct

        payload = capsule_payload(CAPSULE.read_bytes())
        for entry in self.record["components"]:
            data = container.extract(payload, entry["component"])
            cbor_end = 3 + struct.unpack_from(">H", data, 1)[0]
            count = struct.unpack_from("<I", data, cbor_end + 4)[0]
            last = cbor_end + 0x10 + (count - 1) * 0x20
            _, _, sub, _hdr, _body = struct.unpack_from("<5I", data, last)
            meta = data[cbor_end + sub:cbor_end + sub + 0xD0]
            self.assertEqual(meta[0x10:0x20].split(b"\x00")[0].decode("latin-1"), entry["label"])
            self.assertEqual(meta[0x40:0x4C].hex(), entry["nonce"])
            self.assertEqual(meta[0x60:0x70].hex(), entry["tag"])
            self.assertEqual(meta[0xB0:0xD0].hex(), entry["digest_32"])


if __name__ == "__main__":
    unittest.main()
