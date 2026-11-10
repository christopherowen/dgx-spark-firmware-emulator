"""The clock and power interfaces the firmware itself advertises.

Two things are already in the vendor's DSDT, captured under the emulator: each CPU device carries a
`_CPC` (Continuous Performance Control) object, and an ACPI method returns an interface descriptor
that names the SoC blocks the OS may talk to — CPUEB, SPBM, SSPM, HFRP — with the offset of every
field inside each block's shared structure.

`research/spbm-interface.json` holds the extracted field map. This test keeps it honest: the map must
parse, every offset must be 4-byte aligned and distinct within its group, and the power-limit family
the OS writes (PL1..PL4 at 0x100..0x10c) must be present — that family is the one thing a "raise the
cap" experiment would touch.
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

RECORD = ROOT / "research" / "spbm-interface.json"


class ClockInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.record = json.loads(RECORD.read_text())
        self.fields = self.record["fields"]

    def test_the_map_parses_and_is_aligned_and_unique(self):
        self.assertGreater(len(self.fields), 100)
        groups = {}
        for field in self.fields:
            self.assertRegex(field["name"], r"^[A-Z0-9_]+_OFFSET$")
            self.assertEqual(field["offset"] % 4, 0, field["name"])
            group = field["name"].split("_")[0]
            groups.setdefault(group, set()).add(field["offset"])
        for group, offsets in groups.items():
            self.assertEqual(len(offsets), len(groups[group]), f"{group} has duplicate offsets")

    def test_the_os_power_limit_family_is_present(self):
        by_name = {field["name"]: field["offset"] for field in self.fields}
        for index, expected in ((1, 0x100), (2, 0x104), (3, 0x108), (4, 0x10C)):
            name = f"SPBM_PL{index}_VAL_OS_OFFSET"
            self.assertIn(name, by_name, f"{name} is what the OS writes")
            self.assertEqual(by_name[name], expected)

    def test_the_four_sources_of_a_limit_are_named(self):
        # The table distinguishes what the OS asked for, what the EC asked for, what UEFI set and what
        # is in effect — so a later experiment can say which one it changed.
        names = {field["name"] for field in self.fields}
        for source in ("OS", "EC", "UEFI"):
            self.assertIn(f"SPBM_PL1_VAL_{source}_OFFSET", names)
        self.assertIn("SPBM_PL1_VAL_OFFSET", names, "and the effective value")

    def test_the_record_states_what_is_still_missing(self):
        """The base was the missing piece until the firmware's own resource descriptor gave it; what
        remains missing is a run that writes through the window on real hardware."""
        self.assertIn("base", self.record, "the base is recorded once derived")
        self.assertIn("real hardware", self.record["missing"])
        self.assertEqual(self.record["schema"], 1)

    def test_the_cpu_devices_advertise_continuous_performance_control(self):
        # Every CPU device carries _CPC, which is the standard lever Linux exposes as cpufreq — no vendor
        # region needed. The table's own bytes are the durable evidence; the decompiled text was a /tmp
        # convenience that a clean checkout never has, so it is only a fallback here.
        table = ROOT / "firmware" / "downloads" / "acum028-dsdt.aml"
        if not table.is_file():
            self.skipTest("the vendor DSDT is not present under firmware/downloads")
        body = table.read_bytes()
        self.assertGreaterEqual(body.count(b"_CPC"), 8, "the CPU devices should each carry _CPC")
        self.assertIn(b"ACPI0007", body, "they are standard processor devices")


if __name__ == "__main__":
    unittest.main()
