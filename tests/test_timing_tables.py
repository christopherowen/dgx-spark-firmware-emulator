"""Where the DRAM timing tables are, and whether any artifact in the tree carries them.

The answer today is a documented negative: the capsule and the vendor recovery image both ship the
`BCT`, `DDRFSK` and `MISC` partitions erased. The tool reports that per artifact and is ready to
extract and hash them the moment a readback supplies them, which is what a comparison between two
units or two firmware versions would need.
"""

import json
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.timing_tables import TIMING_PARTITIONS, find_map, report  # noqa: E402

RECOVERY = Path("/tmp/usbimg.customer/spirom/dgx_spark_ec_fused.bin")


def synthetic_device(populate=("BCT",)) -> bytes:
    """A whole-device image whose SoC container carries the vendor map, with chosen regions filled."""
    import struct as _struct

    slot = 0x2000000
    image = bytearray(b"\xff" * (2 * slot))
    image[slot:slot + 8] = b"SF_BOOT\x00"
    header = 0x203000
    entries = [(name, 0x100, 0x300000 + index * 0x1000)
               for index, name in enumerate(("SoCFSVN", "BCT", "DDRFSK", "MISC", "BL31"))]
    image[slot + header:slot + header + 8] = b"AAAA\x01\x00\x00\x00"
    cursor = slot + header + 0x10
    for name, size, offset in entries:
        block = (b"BBBB" + _struct.pack("<I", 7) + name.encode().ljust(8, b"\x00")
                 + _struct.pack("<IIII", size, offset, offset + size, 0))
        image[cursor:cursor + len(block)] = block
        cursor += len(block)
        if name in populate:
            start = slot + header + offset          # the map's offsets are relative to the BPT
            image[start:start + size] = bytes(range(size % 251 or 251))[:size]
    return bytes(image)


class TimingTableTests(unittest.TestCase):
    def test_the_magic_finds_the_container_in_either_layout(self):
        image = synthetic_device()
        self.assertEqual(find_map(image), 0x2000000)

    def test_a_populated_region_is_reported_with_its_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "device.bin"
            path.write_bytes(synthetic_device(populate=("BCT",)))
            data = report(path)
        bct = next(part for part in data["partitions"] if part["name"] == "BCT")
        self.assertTrue(bct["populated"])
        self.assertRegex(bct["sha256"], r"^[0-9a-f]{64}$")
        self.assertTrue(data["carries_data"])
        others = [part for part in data["partitions"] if part["name"] != "BCT"]
        self.assertFalse(any(part["populated"] for part in others))

    def test_an_erased_image_carries_nothing_and_says_so(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "device.bin"
            path.write_bytes(synthetic_device(populate=()))
            data = report(path)
        self.assertFalse(data["carries_data"])
        self.assertEqual({part["name"] for part in data["partitions"]},
                         set(TIMING_PARTITIONS))
        self.assertTrue(all(part["sha256"] is None for part in data["partitions"]))

    @unittest.skipUnless(RECOVERY.is_file(), "the recovery part image is not extracted here")
    def test_the_vendor_recovery_image_carries_the_bct_patch_not_timings(self):
        # The finding, kept as a test: BCT holds the four-switch patch, DDRFSK and MISC hold nothing.
        # If a future artifact carries the timing set, this fails and the parser starts from real data.
        data = report(RECOVERY)
        by_name = {part["name"]: part for part in data["partitions"]}
        self.assertTrue(by_name["BCT"]["populated"], "the BCT patch is in the recovery image")
        self.assertFalse(by_name["DDRFSK"]["populated"], "no frequency/timing set there")
        self.assertFalse(by_name["MISC"]["populated"], "nothing in MISC either")
        records = [structure["records"] for structure in data["bct_structures"] if structure["records"]]
        self.assertTrue(records, "the patch's records are found by the magic scan")
        self.assertIn("DRAM_ENCRYPT", records[0])


if __name__ == "__main__":
    unittest.main()
