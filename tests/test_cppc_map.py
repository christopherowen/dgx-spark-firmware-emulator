r"""The _CPC element map, and the fact that two of its registers live outside the MTEL window.

`research/cppc.json` records which processor `_CPC` element sits at which address, derived from the
captured DSDT's 23-element package (NumEntries 23, Revision 3) in the specification's package order.
Two consequences are checked here because both are easy to get wrong: the minimum and maximum request
registers are *not* inside the 0x05170000+0x60000 window `\\_SB.MTEL` declares, and the reference and
delivered performance counters are Functional Fixed Hardware descriptors rather than memory a profile
can seed.
"""

import json
import os
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / "research" / "cppc.json"
def find_dsdt():
    """The decompiled DSDT if this checkout has one.

    It is not carried in this repository (it is vendor-derived), so it comes from `SPARK_DSDT` or
    from `../dsdt.dsl` next to the checkout. Absent, the cross-check is skipped rather than failed.
    """
    override = os.environ.get("SPARK_DSDT")
    if override and Path(override).is_file():
        return Path(override)
    candidate = ROOT.parent / "dsdt.dsl"
    return candidate if candidate.is_file() else None


DECOMPILED = find_dsdt()

MTEL_BASE = 0x05170000
MTEL_SIZE = 0x00060000


class CppcMapTests(unittest.TestCase):
    def setUp(self):
        self.record = json.loads(RECORD.read_text())
        self.entries = {entry["field"]: entry["address"] for entry in self.record["element_map"]}

    def test_the_first_four_performance_registers_are_where_the_package_puts_them(self):
        self.assertEqual(self.entries["HighestPerformance"], "0x051CC404")
        self.assertEqual(self.entries["NominalPerformance"], "0x051CC408")
        self.assertEqual(self.entries["LowestNonlinearPerformance"], "0x051CC40C")
        self.assertEqual(self.entries["LowestPerformance"], "0x051CC410")
        # and they are the CPUEB offset the interface table names, added to MTEL's base
        self.assertEqual(MTEL_BASE + 0x5C404, 0x051CC404)

    def test_two_registers_fall_outside_the_declared_window(self):
        for field in ("MinimumPerformanceRegister", "MaximumPerformanceRegister"):
            address = int(self.entries[field], 16)
            with self.subTest(field=field):
                self.assertLess(address, MTEL_BASE,
                                f"{field} at {address:#x} is below MTEL's window, not inside it")
        self.assertEqual(int(self.entries["MinimumPerformanceRegister"], 16), 0x05150480)
        self.assertEqual(int(self.entries["MaximumPerformanceRegister"], 16), 0x05150500)

    def test_the_counters_are_descriptors_not_cells(self):
        for field in ("ReferencePerformanceCounterRegister", "DeliveredPerformanceCounterRegister"):
            self.assertTrue(self.entries[field].startswith("FFixedHW"), self.entries[field])

    def test_the_record_says_what_it_does_not_prove(self):
        self.assertIn("not_proved", self.record)
        self.assertIn("cpufreq", self.record["not_proved"])

    @unittest.skipUnless(DECOMPILED, "no decompiled DSDT here; set SPARK_DSDT to cross-check")
    def test_the_addresses_come_from_the_table(self):
        text = DECOMPILED.read_text()
        for address in ("0x051CC404", "0x051CC408", "0x051CC40C", "0x051CC410",
                        "0x05150480", "0x05150500"):
            # the disassembler prints these with or without a leading zero, so match the value
            value = int(address, 16)
            pattern = re.compile(r"0x0*%X\b" % value, re.IGNORECASE)
            self.assertRegex(text, pattern, f"{address} is not in the DSDT")


if __name__ == "__main__":
    unittest.main()


class CppcTableAnchorTests(unittest.TestCase):
    """The package the record describes, found in the table's bytes.

    Every processor's `_CPC` method returns `ECPC`, a single `Package` of 23 elements whose first two
    entries are the element count and the revision. That is checkable without a decompiler: `PackageOp`
    is `0x12`, the element count follows the `PkgLength` encoding, and the first two elements are
    `ByteConst`s.
    """

    TABLE = ROOT / "firmware" / "downloads" / "acum028-dsdt.aml"

    def setUp(self):
        if not self.TABLE.is_file():
            self.skipTest("the vendor DSDT is not present under firmware/downloads")
        self.bytes_ = self.TABLE.read_bytes()

    def test_the_package_is_twenty_three_elements_at_revision_three(self):
        at = self.bytes_.find(b"ECPC\x12")
        self.assertGreaterEqual(at, 0, "the package the _CPC methods return")
        # PackageOp, PkgLength (one or more bytes), NumElements
        cursor = at + 5
        lead = self.bytes_[cursor]
        follow = 0 if lead >> 6 == 0 else (lead >> 6)
        cursor += 1 + follow
        self.assertEqual(self.bytes_[cursor], 0x17, "23 elements")
        first = self.bytes_[cursor + 1:cursor + 3]
        second = self.bytes_[cursor + 3:cursor + 5]
        self.assertEqual(first, bytes([0x0A, 0x17]), "element 0: the element count, 23")
        self.assertEqual(second, bytes([0x0A, 0x03]), "element 1: revision 3")

    def test_every_processor_returns_that_package(self):
        self.assertGreaterEqual(self.bytes_.count(b"ECPC"), 4,
                                "one package, returned by several _CPC methods")
