"""The three SMMUv3 instances are recorded so a model can be written against them.

The numbers come from a dumped IORT and cannot be re-derived here without a boot, so what is checked
is the arithmetic and the structure a model depends on: three disjoint windows of the SMMUv3 size,
interrupts that do not collide, and the one instance whose PRI line is absent kept absent rather than
invented.
"""

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / "research" / "smmu-iort.json"
SMMU_V3_SIZE = 0x20000


class SmmuRecordTests(unittest.TestCase):
    def setUp(self):
        self.record = json.loads(RECORD.read_text())
        self.instances = self.record["instances"]

    def test_there_are_three_and_the_iort_says_so(self):
        self.assertEqual(len(self.instances), 3)
        self.assertEqual(self.record["iort_node_summary"]["type_04_smmuv3"], 3)

    def test_each_window_is_the_smmuv3_register_size_and_they_do_not_overlap(self):
        ranges = sorted((int(entry["base"], 16), int(entry["size"], 16)) for entry in self.instances)
        for base, size in ranges:
            self.assertEqual(size, SMMU_V3_SIZE, f"{base:#x} is not one SMMUv3 register window")
        for (start, size), (next_start, _) in zip(ranges, ranges[1:]):
            self.assertLessEqual(start + size, next_start, "windows overlap")

    def test_no_interrupt_is_used_twice(self):
        used = []
        for entry in self.instances:
            used.extend(value for value in entry["irq"].values() if value is not None)
        self.assertEqual(len(used), len(set(used)), f"duplicate GSIV in {used}")
        self.assertTrue(all(32 <= value < 1020 for value in used), "SPI range")

    def test_the_instance_without_a_pri_line_keeps_it_absent(self):
        absent = [entry for entry in self.instances if entry["irq"]["pri"] is None]
        self.assertEqual(len(absent), 1, "exactly one instance has no PRI line in the IORT")
        self.assertEqual(absent[0]["base"], "0x14900000")

    def test_the_qemu_irq_order_is_stated_because_a_swap_is_silent(self):
        order = self.record["qemu_model"]["irq_order"]
        for name in ("event", "PRI", "cmdq sync", "gerror"):
            self.assertIn(name, order)


if __name__ == "__main__":
    unittest.main()
