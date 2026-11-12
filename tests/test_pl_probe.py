"""The power-limit probe: what it may ask, and what the model does when asked anything else.

The six registers come from the eSPI service's own plaintext image. Nothing documents that a normal-world
caller may address them — the fan family is all the firmware documents — so the model, which is built from
that documentation, refuses them. That refusal is the point of the rehearsal: the probe must report it
cleanly rather than crash or retry, before anyone decides whether to ask a unit.
"""

import ctypes
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SOURCE = ROOT / "tests" / "fixtures" / "pl_probe.c"
FAN_MODEL = ROOT / "monitor" / "fan.c"
GUID_HI = 0x20418532A0634A88
GUID_LO = 0x46A5A008C0EEAA83
PL_REGISTERS = (0x06000714, 0x06000718, 0x0600071C, 0x06000720, 0x06000724, 0x06000728)


def fan_model(directory: Path) -> ctypes.CDLL:
    compiler = shutil.which("cc")
    if not compiler:
        raise unittest.SkipTest("a C compiler is required")
    library = directory / "fan.so"
    subprocess.run([compiler, "-shared", "-fPIC", "-O2", "-Wall",
                    "-I", str(ROOT / "monitor"), str(FAN_MODEL), "-o", str(library)], check=True)
    model = ctypes.CDLL(str(library))
    model.fan_request.argtypes = [ctypes.POINTER(ctypes.c_ulong * 18)]
    model.fan_request.restype = ctypes.c_int
    return model


def frame(word: int) -> tuple:
    registers = (ctypes.c_ulong * 18)()
    registers[0] = 0xC400008D
    registers[1] = 0x8003
    registers[2] = GUID_HI
    registers[3] = GUID_LO
    registers[4] = word
    return registers


class PlProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="pl-probe-")
        cls.model = fan_model(Path(cls.directory.name))

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_the_documented_fan_read_is_served(self):
        registers = frame(0x0C | (0x06000704 << 8) | (2 << 40))
        self.assertEqual(self.model.fan_request(ctypes.byref(registers)), 1)
        self.assertEqual(registers[0], 0xC400008E)

    def test_a_power_limit_read_is_not_served_by_the_model(self):
        for register in PL_REGISTERS:
            with self.subTest(register=hex(register)):
                registers = frame(0x0C | (register << 8) | (4 << 40))
                self.assertEqual(self.model.fan_request(ctypes.byref(registers)), 0,
                                 "the model is built from the documentation, which names the fan family only")

    def test_a_wrong_service_constant_is_not_served(self):
        registers = frame(0x0C | (0x06000704 << 8) | (2 << 40))
        registers[2] = 0
        self.assertEqual(self.model.fan_request(ctypes.byref(registers)), 0)

    def test_the_payload_contains_no_write_opcode(self):
        """The probe asks, it never sets: opcode 0x09 must not appear in its request construction."""
        text = SOURCE.read_text()
        self.assertNotIn("0x09ULL |", text)
        self.assertNotIn("0x09 |", text)
        self.assertIn("0x0cULL", text, "the read opcode is the only one it builds")
        self.assertIn("allowed(command)", text, "and it refuses a register outside the six")


if __name__ == "__main__":
    unittest.main()

