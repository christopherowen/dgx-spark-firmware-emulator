"""Whether a firmware declares fan devices, read from the AML itself rather than from a grep of the ASL.

`EisaId("PNP0C0B")` is stored in AML as `DWordPrefix 0x0C` followed by the four EISA bytes; for a fan that
is `0c 41 d0 0c 0b`. Counting that constant and the `_FSL`/`_TMP` name signatures answers, from the vendor
bytes, why one firmware's fans are in `/sys/class/thermal` and another's are not.

Both tables are vendor content and are not committed: the test runs when they are present under
`firmware/` and skips otherwise, so its evidence is local but its claim is checkable.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# DWordConst prefix + the four EISA bytes of PNP0C0B, little-endian value 0x0B0CD041.
FAN_EISA_ID = bytes.fromhex("0c41d00c0b")
TABLES = {
    "acum028": ROOT / "firmware" / "downloads" / "acum028-dsdt.aml",
    "acum027": ROOT / "firmware" / "downloads" / "acum027-dsdt.aml",
}
EXPECTED = {
    "acum028": {"fan_ids": 2, "fsl": 2, "tmp": 9},
    "acum027": {"fan_ids": 0, "fsl": 0, "tmp": 9},
}


def counts(path: Path) -> dict:
    data = path.read_bytes()
    return {"fan_ids": data.count(FAN_EISA_ID), "fsl": data.count(b"_FSL"), "tmp": data.count(b"_TMP"),
            "size": len(data)}


class FanPresenceTests(unittest.TestCase):
    def test_each_present_table_matches_its_record(self):
        ran = 0
        for name, path in TABLES.items():
            if not path.is_file():
                continue
            with self.subTest(firmware=name):
                ran += 1
                found = counts(path)
                for key, value in EXPECTED[name].items():
                    self.assertEqual(found[key], value, f"{name} {key}")
        if ran == 0:
            self.skipTest("neither vendor table is present under firmware/downloads")

    def test_the_difference_is_the_fan_device_and_not_the_thermal_zones(self):
        if not all(path.is_file() for path in TABLES.values()):
            self.skipTest("both vendor tables are needed to compare")
        old, new = counts(TABLES["acum027"]), counts(TABLES["acum028"])
        self.assertEqual(old["tmp"], new["tmp"], "both report temperatures")
        self.assertEqual(old["fan_ids"], 0, "the older firmware declares no fan device")
        self.assertGreater(new["fan_ids"], 0, "the newer one does")


if __name__ == "__main__":
    unittest.main()
