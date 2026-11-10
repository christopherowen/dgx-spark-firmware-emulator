r"""The base address of the power-limit structure, and the arithmetic that derives it.

`research/spbm-interface.json` names the offsets inside the firmware's shared power structure but not
where it lives — and `tools/pl_limits.py` refuses to write without a base for exactly that reason.
The firmware does state it: `\\_SB.MTEL` (a `NVDA8800` device) declares `Memory32Fixed(0x05170000,
0x00060000)` in its `_CRS`, and the processor `_CPC` objects return registers that land inside it at
the offsets this same table names for CPUEB. This test checks the arithmetic that ties the two
together, and cross-checks it against the decompiled DSDT when that file is present.

What the arithmetic establishes is *where the `_CPC` registers are read*. It does not establish a
writable SPBM block, its units, or how SPBM arbitrates an OS request: the DSDT lists CPUEB and SPBM
separately, carries an `UPDATE_SPBM` descriptor, and declares MTEL's resources ReadOnly.
"""

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / "research" / "spbm-interface.json"
# The durable anchor is the table's own bytes.
TABLE = ROOT / "firmware" / "downloads" / "acum028-dsdt.aml"


class PowerInterfaceBaseTests(unittest.TestCase):
    def setUp(self):
        self.record = json.loads(RECORD.read_text())

    def test_the_base_is_recorded_with_the_list_that_derives_it(self):
        base = self.record.get("base")
        self.assertIsNotNone(base, "the record states where the structure lives")
        self.assertEqual(base["value"], 0x5170000)
        self.assertGreaterEqual(len(base["checks"]), 2, "the derivation is written down, not asserted")

    def test_the_offsets_in_the_table_fall_inside_the_declared_region(self):
        base = self.record["base"]["value"]
        region_size = 0x60000
        cppc = 0x5C404                     # CPUEB's CPPC_INTERFACE, from this same table
        self.assertEqual(base + cppc, 0x51CC404,
                         "the first _CPC register the DSDT returns")
        self.assertLess(base + cppc, base + region_size, "inside the declared region")
        for offset in (0x100, 0x160, 0x5B400):   # OS PL1, effective PL1, PL_INTERFACE
            with self.subTest(offset=hex(offset)):
                self.assertLess(base + offset, base + region_size)

    @unittest.skipUnless(TABLE.is_file(), "the vendor DSDT is not present under firmware/downloads")
    def test_the_bytes_declare_the_region_and_the_register(self):
        """`Memory32Fixed` is descriptor 0x86 with a nine-byte body: base and length, little-endian."""
        import struct

        body = TABLE.read_bytes()
        descriptor = b"\x86\x09\x00\x00" + struct.pack("<I", 0x05170000) + struct.pack("<I", 0x00060000)
        self.assertIn(descriptor, body, "MTEL's resource descriptor, as bytes")
        self.assertIn(b"MTEL", body, "the device that carries it")
        self.assertIn(b"NVDA8800", body, "its hardware id")
        self.assertIn(b"\x0c" + struct.pack("<I", 0x051CC404), body,
                      "the first _CPC register the derivation checks against")


if __name__ == "__main__":
    unittest.main()
