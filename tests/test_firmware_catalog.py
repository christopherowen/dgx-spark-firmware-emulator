"""Which firmware is newest, and which artifact this repository actually holds.

The question "does the fan work on the latest firmware" needs the newest *available* release, not the
newest file lying around. `research/firmware-catalog.json` records the LVFS catalog as read on
2026-11-09 — one release per DGX Spark component — and which local capsule it corresponds to. This
test keeps the record tied to the artifacts: the local cab's own version string and hash must be the
ones the record names.
"""

import hashlib
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / "research" / "firmware-catalog.json"
DOWNLOADS = ROOT / "firmware" / "downloads"

VERSION_PATTERN = re.compile(rb"0x20[0-9a-f]{5}")


class FirmwareCatalogTests(unittest.TestCase):
    def setUp(self):
        self.record = json.loads(RECORD.read_text())

    def test_the_catalog_names_one_release_per_dgx_spark_component(self):
        components = self.record["components"]
        self.assertIn("com.nvidia.dgx.spark.socfw.firmware", components)
        self.assertIn("com.nvidia.dgx.spark.ec.firmware", components)
        for identifier, component in components.items():
            self.assertEqual(len(component["releases"]), 1,
                             f"{identifier} publishes exactly one release")

    def test_the_answer_is_stated_with_a_date(self):
        answer = self.record["answer"]
        self.assertIn("0x2009e0f", answer)
        self.assertIn("2026-09-28", answer)

    def test_the_newest_capsule_is_the_one_this_repository_holds(self):
        newest = self.record["components"]["com.nvidia.dgx.spark.socfw.firmware"]["releases"][0]
        local = self.record["local"]["spark-soc-2.158.15.cab"]
        self.assertEqual(local["version"], newest["version"])
        self.assertEqual(local["cab_sha256"], newest["cab_sha256"])
        cab = DOWNLOADS / "spark-soc-2.158.15.cab"
        if not cab.is_file():
            self.skipTest("the capsule is not downloaded here")
        self.assertEqual(hashlib.sha256(cab.read_bytes()).hexdigest(), newest["cab_sha256"])
        self.assertIn(newest["version"].encode(), cab.read_bytes(),
                      "the capsule carries its own version string")

    def test_the_dell_capsule_is_recorded_as_a_different_product(self):
        dell = self.record["local"]["spark-soc-4.1.0.cab"]
        self.assertIsNone(dell["version"])
        self.assertIn("Dell", dell["note"])


if __name__ == "__main__":
    unittest.main()
