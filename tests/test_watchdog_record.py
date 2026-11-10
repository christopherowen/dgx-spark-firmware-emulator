"""The watchdog-period record: what was searched, and what it does or does not establish.

The model's reboot cycle is its own; the board's interval is not established. That is a
claim in its own right, so it carries the artifacts it was searched in, with their hashes,
and the searches are re-run here against whatever is present. A record that said "not
established" without saying where it looked would be a shrug, not evidence.
"""

import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

RECORD = ROOT / "research" / "watchdog-periods.json"
PREPARED = ROOT / "firmware" / "prepared"

# The two frames of the board's SBSA generic watchdog, as the profile maps them.
WATCHDOG_ADDRESSES = (0x1C041000, 0x1C042000)


class WatchdogRecordTests(unittest.TestCase):
    def setUp(self):
        self.record = json.loads(RECORD.read_text())

    def test_the_record_says_which_question_it_answers(self):
        self.assertEqual(self.record["schema"], 1)
        self.assertIn("interval", self.record["question"].lower())
        self.assertIsInstance(self.record["established"], bool)

    def test_an_established_value_would_have_to_carry_its_evidence(self):
        # The record is refused if it ever claims a value without saying how, or claims none
        # while listing a number — either direction would be a claim without evidence.
        if self.record["established"]:
            self.assertTrue(self.record.get("value"), "an established answer needs a value")
            self.assertTrue(self.record.get("derivation"), "and a derivation")
        else:
            self.assertNotIn("value", self.record)
        for entry in self.record["searched"]:
            for field in ("artifact", "sha256", "method", "finding"):
                self.assertTrue(entry.get(field), f"{entry.get('artifact')} is missing {field}")

    def test_every_searched_artifact_is_named_by_its_hash(self):
        for entry in self.record["searched"]:
            self.assertRegex(entry["sha256"], r"^[0-9a-f]{64}$")

    def test_the_search_findings_still_hold_where_the_artifacts_exist(self):
        # Gated on the prepared release: on a clean clone there is nothing to search, and the
        # record's numbers are then the only evidence there is.
        directory = PREPARED / "2.155.11"
        if not directory.is_dir():
            self.skipTest(f"{directory} is not prepared; the record's hashes stand alone")
        for entry in self.record["searched"]:
            path = ROOT / entry["artifact"]
            if not path.is_file():
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(digest, entry["sha256"],
                             f"{entry['artifact']} is not the artifact this record describes")

        uefi = (directory / "uefi.fd").read_bytes()
        for address in WATCHDOG_ADDRESSES:
            needle = address.to_bytes(8, "little")
            self.assertEqual(uefi.count(needle), 1,
                             f"{address:#x} should occur once, as platform description data")

    def test_the_model_does_not_claim_a_default_interval(self):
        behaviour = self.record["model_behaviour"]
        self.assertIsNone(behaviour["default_preload_ms"],
                          "the model must not preload an interval it cannot justify")
        self.assertIn("ws1", behaviour["stages"], "the reset stage must be named")


if __name__ == "__main__":
    unittest.main()
