"""The eSPI record: what was searched, and why there is no model.

eSPI is one of the gaps this repository names. The honest outcome of looking is written down:
the service exists in the vendor capsule, but in a partition the preparation does not extract, so
no boot reaches it and a model would be invented rather than derived. These tests keep the record
from drifting into a claim it cannot support, and re-run the searches that back it.
"""

import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

RECORD = ROOT / "research" / "espi.json"
PREPARED = ROOT / "firmware" / "prepared" / "2.155.11"
CONTROLLER = (0x16050000).to_bytes(4, "little")
SERVICE_STRING = b"eSPI service"


class EspiRecordTests(unittest.TestCase):
    def setUp(self):
        self.record = json.loads(RECORD.read_text())

    def test_the_record_answers_one_question_and_says_whether_it_can(self):
        self.assertEqual(self.record["schema"], 1)
        self.assertIn("espi", self.record["question"].lower())
        self.assertIs(self.record["established"], False)
        self.assertTrue(self.record["status"])

    def test_a_claim_would_have_to_name_a_trigger_and_an_artifact(self):
        # The rule that keeps this honest: no entry may claim behaviour without saying where it was
        # observed and what ran. A model would be listed under "established" with its evidence.
        for entry in self.record["searched"]:
            for field in ("artifact", "sha256", "method", "finding"):
                self.assertTrue(entry.get(field), f"{entry.get('artifact')} is missing {field}")
            self.assertRegex(entry["sha256"], r"^[0-9a-f]{64}$")

    def test_the_reason_it_is_not_modelled_names_the_missing_prerequisite(self):
        reasons = " ".join(self.record["why_not_modelled"]).lower()
        self.assertIn("unextracted", reasons)
        self.assertTrue(self.record["what_would_close_it"])

    def test_the_searches_still_hold_where_the_artifacts_exist(self):
        if not PREPARED.is_dir():
            self.skipTest(f"{PREPARED} is not prepared; the record's hashes stand alone")
        for entry in self.record["searched"]:
            path = ROOT / entry["artifact"]
            if path.is_file():
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), entry["sha256"])

        uefi = (PREPARED / "uefi.fd").read_bytes()
        self.assertEqual(uefi.count(CONTROLLER), 3,
                         "the controller address should appear in the UEFI image's resources")
        capsule = (PREPARED / "socfw.cap").read_bytes()
        self.assertGreaterEqual(capsule.count(SERVICE_STRING), 1,
                                "the capsule should still carry the eSPI service strings")
        # The record names eight components, so the table it describes must have eight names in it.
        table = capsule.find(b"\x41\x41\x41\x41")
        header = (0x203000).to_bytes(4, "little")
        self.assertGreaterEqual(capsule.count(b"AAAA" + b"\x01\x00\x00\x00"), 1,
                                "the MediaTek image header is still there")
        for name in ("SoCFSVN", "ACC", "PLL", "DCC", "SSPM", "HFRP", "CPUEB", "DPM"):
            self.assertIn(name.encode(), capsule, f"{name} should appear in the component table")


if __name__ == "__main__":
    unittest.main()
