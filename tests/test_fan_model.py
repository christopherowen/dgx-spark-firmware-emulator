r"""The fan service model, driven with the exact requests the firmware's DSDT builds.

`_SB.FAN0.WFCF` converts a level 0..100 into an rpm and sends it as two little-endian bytes per
fan through SMCCC 0xC400008D; `_SB.FAN0._FST` reads the rpm back with request
`0x0C | (0x06000704 << 8) | (2 << 40)`. The model in `monitor/fan.c` is compiled for the host and
driven here, so the arithmetic and the request decoding are checked without a machine.

What this does not test: the EC. The model is what the firmware says the interface is; no result
about a real fan follows from it.
"""

import ctypes
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "monitor" / "fan.c"
GUID_HI = 0x20418532A0634A88
GUID_LO = 0x46A5A008C0EEAA83
FMIN, FMAX = 975, 4350


class FanModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("cc"):
            raise unittest.SkipTest("a C compiler is required")
        cls.directory = tempfile.TemporaryDirectory(prefix="fan-model-")
        library = Path(cls.directory.name) / "fan.so"
        subprocess.run(["cc", "-shared", "-fPIC", "-Wall", "-Wextra", "-Werror",
                        "-I", str(ROOT / "monitor"), str(SOURCE), "-o", str(library)], check=True)
        cls.fan = ctypes.CDLL(str(library))
        cls.fan.fan_request.argtypes = [ctypes.POINTER(ctypes.c_ulong)]
        cls.fan.fan_request.restype = ctypes.c_int
        cls.fan.fan_rpm.argtypes = [ctypes.c_uint]
        cls.fan.fan_rpm.restype = ctypes.c_uint
        cls.fan.fan_rpm_for_level.argtypes = [ctypes.c_uint]
        cls.fan.fan_rpm_for_level.restype = ctypes.c_uint

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def call(self, request):
        # The service clears the register file up to x17, as the RTC service does, so the frame has
        # to be a whole SMC register file — a five-register frame is a buffer overrun.
        frame = (ctypes.c_ulong * 31)(0xC400008D, 0x8003, GUID_HI, GUID_LO, request)
        return self.fan.fan_request(frame), frame

    def test_the_firmware_arithmetic_is_reproduced(self):
        for level, rpm in ((0, FMIN), (50, FMIN + (FMAX - FMIN) // 2), (100, FMAX)):
            with self.subTest(level=level):
                self.assertEqual(self.fan.fan_rpm_for_level(level), rpm)

    def test_a_level_is_written_as_two_bytes_per_fan_and_reads_back(self):
        for level in (0, 50, 100):
            rpm = FMIN + ((FMAX - FMIN) * level) // 100
            for command, value in ((0x06000790, rpm & 0xFF), (0x06000791, rpm >> 8),
                                   (0x06000792, rpm & 0xFF), (0x06000793, rpm >> 8)):
                handled, frame = self.call(0x09 | (command << 8) | (value << 40))
                self.assertEqual(handled, 1)
                self.assertEqual(frame[0], 0xC400008E, "the completion id must come back in x0")
            handled, frame = self.call(0x0C | (0x06000704 << 8) | (2 << 40))
            self.assertEqual(handled, 1)
            self.assertEqual(frame[4] & 0xFFFF, rpm, f"level {level} reads back {frame[4] & 0xFFFF}")

    def test_the_two_fans_are_independent(self):
        """Four sub-commands, two fans: a model that answered every write with one fan's state would
        pass a test that only ever wrote the same value to both."""
        for command, value in ((0x06000790, 0xCF), (0x06000791, 0x03)):          # fan 0 -> 975
            self.call(0x09 | (command << 8) | (value << 40))
        for command, value in ((0x06000792, 0xFE), (0x06000793, 0x10)):          # fan 1 -> 4350
            self.call(0x09 | (command << 8) | (value << 40))
        self.assertEqual(self.fan.fan_rpm(0), 975)
        self.assertEqual(self.fan.fan_rpm(1), 4350)

    def test_byte_order_does_not_depend_on_write_order(self):
        """The high byte is written after the low one, and either may be repeated; the rpm is what
        the two bytes say, not what order they arrived in."""
        for command, value in ((0x06000791, 0x10), (0x06000790, 0xFE)):          # high first
            self.call(0x09 | (command << 8) | (value << 40))
        handled, frame = self.call(0x0C | (0x06000704 << 8) | (2 << 40))
        self.assertEqual(handled, 1)
        self.assertEqual(frame[4] & 0xFFFF, 4350)

    def test_a_level_above_one_hundred_is_clamped(self):
        self.assertEqual(self.fan.fan_rpm_for_level(1000), FMAX)

    def test_each_fan_has_its_own_status_command(self):
        """\\_SB.FAN0._FST reads 0x06000704 and \\_SB.FAN1._FST reads 0x06000706; a model that
        answered one command for both would report fan 0's speed as fan 1's."""
        for command, value in ((0x06000790, 0xCF), (0x06000791, 0x03)):          # fan 0 -> 975
            self.call(0x09 | (command << 8) | (value << 40))
        for command, value in ((0x06000792, 0xFE), (0x06000793, 0x10)):          # fan 1 -> 4350
            self.call(0x09 | (command << 8) | (value << 40))
        for status, expected in ((0x06000704, 975), (0x06000706, 4350)):
            handled, frame = self.call(0x0C | (status << 8) | (2 << 40))
            self.assertEqual(handled, 1, f"status {status:#x} is answered")
            self.assertEqual(frame[4] & 0xFFFF, expected, f"status {status:#x}")

    def test_the_endpoint_is_echoed_in_the_reply(self):
        handled, frame = self.call(0x0C | (0x06000704 << 8) | (2 << 40))
        self.assertEqual(handled, 1)
        self.assertEqual(frame[1], ((0x8003 & 0xFFFF) << 16) | ((0x8003 >> 16) & 0xFFFF))

    def test_another_service_guid_is_not_claimed(self):
        frame = (ctypes.c_ulong * 31)(0xC400008D, 0x8003, 0x1111111111111111, GUID_LO, 0)
        self.assertEqual(self.fan.fan_request(frame), 0)

    def test_an_unknown_command_is_refused_rather_than_answered(self):
        handled, _frame = self.call(0x09 | (0x06000799 << 8) | (1 << 40))
        self.assertEqual(handled, 0)
        handled, _frame = self.call(0x0C | (0x06000799 << 8) | (2 << 40))
        self.assertEqual(handled, 0)

    def test_a_different_function_id_is_not_ours(self):
        frame = (ctypes.c_ulong * 31)(0xC400008C, 0x8003, GUID_HI, GUID_LO, 0)
        self.assertEqual(self.fan.fan_request(frame), 0)


if __name__ == "__main__":
    unittest.main()
