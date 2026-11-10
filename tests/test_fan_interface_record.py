"""The fan interface record, checked against the model.

`research/fan-interface.json` holds what the vendor's DSDT says: fine-grained control with step 1,
the level-to-rpm arithmetic, and one status command per fan. The model is what runs. This test keeps
the record tied to behaviour instead of to a summary someone typed once — the numbers are obtained by
calling the model, not by trusting the file.
"""

import ctypes
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / "research" / "fan-interface.json"


class FanInterfaceRecordTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("cc"):
            raise unittest.SkipTest("a C compiler is required")
        cls.record = json.loads(RECORD.read_text())
        cls.directory = tempfile.TemporaryDirectory(prefix="fan-record-")
        library = Path(cls.directory.name) / "fan.so"
        subprocess.run(["cc", "-shared", "-fPIC", "-Wall", "-Wextra", "-Werror",
                        "-I", str(ROOT / "monitor"), str(ROOT / "monitor" / "fan.c"),
                        "-o", str(library)], check=True)
        cls.fan = ctypes.CDLL(str(library))
        cls.fan.fan_request.argtypes = [ctypes.POINTER(ctypes.c_ulong)]
        cls.fan.fan_request.restype = ctypes.c_int
        cls.fan.fan_rpm_for_level.argtypes = [ctypes.c_uint]
        cls.fan.fan_rpm_for_level.restype = ctypes.c_uint

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def call(self, request):
        guid = self.record["smc"]["guid"]
        frame = (ctypes.c_ulong * 31)(0xC400008D, self.record["smc"]["endpoint"],
                                     int(guid[0], 16), int(guid[1], 16), request)
        return self.fan.fan_request(frame), frame

    def test_the_level_arithmetic_matches_what_the_model_computes(self):
        low, high = self.record["interface"]["level_range"]
        self.assertEqual((low, high), (0, 100))
        self.assertEqual(self.fan.fan_rpm_for_level(0), 975, "FMIN as the DSDT declares it")
        self.assertEqual(self.fan.fan_rpm_for_level(100), 4350, "FMAX as the DSDT declares it")
        self.assertIn("975", self.record["interface"]["level_zero_meaning"])

    def test_every_command_the_record_names_is_answered_the_way_it_says(self):
        smc = self.record["smc"]
        for command in list(smc["write_commands"]) + list(smc["read_commands"]):
            with self.subTest(command=command):
                value = 0x0C if command in smc["read_commands"] else 0x09
                handled, frame = self.call(value | (int(command, 16) << 8) | (2 << 40))
                self.assertEqual(handled, 1, f"{command} is part of the interface")
                self.assertEqual(frame[0], 0xC400008E, "the completion id comes back in x0")

    def test_the_status_commands_are_one_per_fan(self):
        read = [int(command, 16) for command in self.record["smc"]["read_commands"]]
        self.assertEqual(read, [0x06000704, 0x06000706],
                         "a single status command for both fans is the mistake this records")

    def test_the_fine_grained_step_is_one(self):
        fif = self.record["interface"]["FIF"]["returns"]
        self.assertEqual(fif[1], 1, "fine-grained control")
        self.assertEqual(fif[2], 1, "step size, which is what makes state equal level")


if __name__ == "__main__":
    unittest.main()


class FanPartitionIdentityTests(unittest.TestCase):
    """The fan service's constants are a partition UUID, and the live map knows that partition."""

    def test_the_constants_are_the_live_partition_uuid(self):
        import json
        import struct

        record = json.loads((ROOT / "research" / "ffa-partitions-live.json").read_text())
        interface = json.loads((ROOT / "research" / "fan-interface.json").read_text())
        high = int(str(interface["smc"]["guid"][0]), 0) if isinstance(interface["smc"]["guid"][0], str) \
            else interface["smc"]["guid"][0]
        low = int(str(interface["smc"]["guid"][1]), 0) if isinstance(interface["smc"]["guid"][1], str) \
            else interface["smc"]["guid"][1]
        raw = struct.pack("<QQ", high, low)
        a, b, c = struct.unpack_from(">IHH", raw, 0)
        uuid = f"{a:08x}-{b:04x}-{c:04x}-{raw[8:10].hex()}-{raw[10:16].hex()}"
        self.assertIn(uuid, record["partitions"]["0x8003"],
                      "the endpoint's constants identify the partition the firmware enumerates")


class FanRecordAgainstTheTableTests(unittest.TestCase):
    """The record's constants, re-derived from the vendor table itself when it is present.

    Most of the tests above compare the record with the model, which is a second transcription of the same
    reading — they would agree even if the reading were wrong. This one looks for each constant in the AML
    bytes, in the encoding the table actually uses:

    * `DWordConst` (opcode `0x0c`) for the SMC function ids and for every register id
      (`XX0 = 0xC400008D`, `WRB (0x06000790, …)`, `XX4 = 0x000002060007040C`);
    * `QWordConst` (opcode `0x0e`) for the two service words, which are one UUID read little-endian;
    * `WordConst` (opcode `0x0b`) for the rpm endpoints `FMIN`/`FMAX`.
    """

    TABLE = ROOT / "firmware" / "downloads" / "acum028-dsdt.aml"

    def setUp(self):
        if not self.TABLE.is_file():
            self.skipTest("the vendor DSDT is not present under firmware/downloads")
        self.bytes_ = self.TABLE.read_bytes()
        self.record = json.loads((ROOT / "research" / "fan-interface.json").read_text())

    @staticmethod
    def dword(value: int) -> bytes:
        import struct
        return b"\x0c" + struct.pack("<I", value)

    @staticmethod
    def qword(value: int) -> bytes:
        import struct
        return b"\x0e" + struct.pack("<Q", value)

    @staticmethod
    def word(value: int) -> bytes:
        import struct
        return b"\x0b" + struct.pack("<H", value)

    def test_the_smc_function_ids_are_in_the_table(self):
        for value in (0xC400008D, 0xC400008E):
            self.assertIn(self.dword(value), self.bytes_, f"{value:#x}")

    def test_the_two_service_words_are_in_the_table(self):
        for text in self.record["smc"]["guid"]:
            self.assertIn(self.qword(int(str(text), 0)), self.bytes_, f"{text}")

    def test_the_register_ids_are_in_the_table(self):
        ids = [0x06000790, 0x06000791, 0x06000792, 0x06000793, 0x06000704, 0x06000706]
        iface = self.record.get("interface", {})
        for value in iface.get("commands", []):
            ids.append(int(str(value), 0) if isinstance(value, str) else value)
        for value in ids:
            self.assertIn(self.dword(value), self.bytes_, f"{value:#x}")

    def test_the_rpm_endpoints_come_from_the_table(self):
        for value in (975, 4350):
            self.assertIn(self.word(value), self.bytes_, f"{value} rpm")
