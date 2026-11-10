"""Where each component's payload begins, and what the bytes there are.

The map gives a component's extent; the component itself gives the offset of its payload (CBOR header
+ sub-image offset + a 0xd0 header) and the payload's first bytes say what it is. That is a second,
independent check that the slice is right: a window shifted by even one byte does not put SPKG at the
body offset of STMM, FLASH and ESPI, nor FILEINFO at the body offset of UEFI.
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import container  # noqa: E402
from tools.spark_image import capsule_payload  # noqa: E402

RECORD = ROOT / "research" / "component-bodies.json"
CAPSULE = ROOT / "firmware" / "prepared" / "2.158.15" / "socfw.cap"


class ComponentBodyTests(unittest.TestCase):
    def setUp(self):
        self.record = json.loads(RECORD.read_text())

    def test_the_record_says_what_it_found_and_what_it_did_not(self):
        """Documentation lint: the superseded empty-manifest claim stays explicitly retracted."""
        claim = self.record["invalidated_findings"]["empty_manifest"]
        self.assertEqual(claim["status"], "retracted")
        self.assertEqual(claim["replacement_record"], "research/spkg-manifest.json")
        self.assertTrue(self.record["finding"])
        self.assertTrue(self.record["not_a_finding"])

    def test_the_wrapped_components_name_themselves_at_the_body(self):
        kinds = {name: entry["kind"] for name, entry in self.record["components"].items()}
        for name in ("STMM", "FLASH", "ESPI"):
            self.assertEqual(kinds[name], "SPKG v2", f"{name} carries a signed package")
        self.assertEqual(kinds["UEFI"], "MediaTek FILEINFO")
        for name in ("BL31", "BL32", "TPM", "SCP", "GPU", "SPBM"):
            self.assertEqual(kinds[name], "opaque",
                             f"{name} has no container magic at its body — signed or encrypted content")

    @unittest.skipUnless(CAPSULE.is_file(), "the prepared capsule is not present")
    def test_the_bodies_match_the_capsule(self):
        payload = capsule_payload(CAPSULE.read_bytes())
        self.assertEqual(container.header_position(payload), self.record["source"]["header_position"])
        import hashlib
        self.assertEqual(hashlib.sha256(CAPSULE.read_bytes()).hexdigest(),
                         self.record["source"]["capsule_sha256"])
        for name, entry in self.record["components"].items():
            data = container.extract(payload, name)
            self.assertEqual(data[entry["body_at"]:entry["body_at"] + 8].hex(), entry["first_bytes"],
                             f"{name} body bytes")
            self.assertEqual(container.self_described_extent(data), len(data), f"{name} extent")


if __name__ == "__main__":
    unittest.main()
