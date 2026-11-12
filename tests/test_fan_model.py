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
        packet_library = Path(cls.directory.name) / "fan-packet.so"
        subprocess.run(["cc", "-shared", "-fPIC", "-Wall", "-Wextra", "-Werror",
                        "-DSPARK_FFA_EC=1", "-I", str(ROOT / "monitor"), str(SOURCE),
                        "-o", str(packet_library)], check=True)
        cls.packet = ctypes.CDLL(str(packet_library))
        cls.packet.fan_packet_request.argtypes = [ctypes.POINTER(ctypes.c_ulong)]
        cls.packet.fan_packet_request.restype = ctypes.c_int
        cls.packet.fan_rpm.argtypes = [ctypes.c_uint]
        cls.packet.fan_rpm.restype = ctypes.c_uint

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def call(self, request):
        # The service clears the register file up to x17, as the RTC service does, so the frame has
        # to be a whole SMC register file — a five-register frame is a buffer overrun.
        frame = (ctypes.c_ulong * 31)(0xC400008D, 0x8003, GUID_HI, GUID_LO, request)
        return self.fan.fan_request(frame), frame

    def packet_call(self, payload, v2=True, endpoint=0x8003,
                    uuid=(0x86491DD2804DB078, 0xC57A24607B46CB8A)):
        frame = (ctypes.c_ulong * 31)()
        frame[0], frame[1] = (0xC400008D if v2 else 0xC400006F), endpoint
        if v2:
            frame[2], frame[3] = uuid
        start = 4 if v2 else 3
        ctypes.memmove(ctypes.addressof(frame) + start * 8, payload, len(payload))
        handled = self.packet.fan_packet_request(frame)
        raw = bytes(frame)[start * 8:(18 if v2 else 8) * 8]
        return handled, frame, raw

    def packet_operation(self, operation, inputs=b"", out=0, v2=True):
        handled, frame, raw = self.packet_call(
            bytes((1, operation, len(inputs), out)) + inputs, v2=v2)
        self.assertEqual(handled, 1)
        self.assertEqual(frame[0], 0xC400008E if v2 else 0xC4000070)
        self.assertEqual(frame[1], 0x80030000)
        self.assertEqual(raw[:4], b"\0" * 4)
        handled, _, raw = self.packet_call(b"\2", v2=v2)
        self.assertEqual(handled, 1)
        self.assertEqual(raw[0], 0)
        return raw[1:]

    def test_the_firmware_arithmetic_is_reproduced(self):
        for level, rpm in ((0, FMIN), (50, FMIN + (FMAX - FMIN) // 2), (100, FMAX)):
            with self.subTest(level=level):
                self.assertEqual(self.fan.fan_rpm_for_level(level), rpm)
        caps = self.packet_operation(1, out=10)
        self.assertEqual(caps[:10], bytes.fromhex("0100ec0428236207bc34"))

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
        for v2 in (False, True):
            for floor, expected in ((4500, (4500, 4500)),
                                    (13500, (9000, 13500)),
                                    (0, (0, 0)), (0xFFFF, (1260, 1890))):
                with self.subTest(v2=v2, floor=floor):
                    self.packet_operation(5, floor.to_bytes(2, "little"), v2=v2)
                    reply = self.packet_operation(4, out=2, v2=v2)
                    self.assertEqual(int.from_bytes(reply[:2], "little"), floor)
                    telemetry = self.packet_operation(7, out=64, v2=v2)
                    self.assertEqual(tuple(int.from_bytes(telemetry[i:i + 2], "little")
                                           for i in (4, 6)), expected)
                    self.assertEqual(tuple(self.packet.fan_rpm(i) for i in (0, 1)), expected)

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
        self.assertEqual(self.packet_call(b"\2", uuid=(0, 0))[0], 0)
        self.assertEqual(self.packet_call(b"\2", endpoint=0x8004)[0], 0)
        self.assertFalse(hasattr(self.fan, "fan_packet_request"),
                         "the default monitor must not expose the gated service")

    def test_an_unknown_command_is_refused_rather_than_answered(self):
        handled, _frame = self.call(0x09 | (0x06000799 << 8) | (1 << 40))
        self.assertEqual(handled, 0)
        handled, _frame = self.call(0x0C | (0x06000799 << 8) | (2 << 40))
        self.assertEqual(handled, 0)
        before = self.packet_operation(4, out=2)[:2]
        for payload in (b"\x01\x05\x01\x00\xff", b"\x01\x04\x00\x03",
                        b"\x01\xff\x00\x00", b"\x03"):
            with self.subTest(payload=payload):
                handled, _, raw = self.packet_call(payload)
                self.assertEqual(handled, 1)
                self.assertNotEqual(int.from_bytes(raw[:4], "little"), 0)
                self.assertEqual(self.packet_operation(4, out=2)[:2], before)

    def test_a_different_function_id_is_not_ours(self):
        frame = (ctypes.c_ulong * 31)(0xC400008C, 0x8003, GUID_HI, GUID_LO, 0)
        self.assertEqual(self.fan.fan_request(frame), 0)


if __name__ == "__main__":
    unittest.main()
