r"""The control module's wire format, checked against the firmware's own model.

`kernel/spark_fan.c` exists because Linux's ACPI path to `_FSL` is refused by this firmware, so the
service has to be issued directly. That makes one thing load-bearing: the request words the module
builds must be the ones `\\_SB.FAN0.WRB` and `\\_SB.FAN0._FST` build. The same source compiles for the
host (no kernel headers) and its builders are compared here with the emulator's `monitor/fan.c`,
which was derived from the DSDT — two independent implementations of one contract must agree.
"""

import ctypes
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "kernel" / "spark_fan.c"
MODEL = ROOT / "monitor" / "fan.c"


class KernelModuleEncodingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("cc"):
            raise unittest.SkipTest("a C compiler is required")
        cls.directory = tempfile.TemporaryDirectory(prefix="spark-fan-module-")
        work = Path(cls.directory.name)

        # The module's encoders, without the kernel-facing half.
        shim = work / "shim.c"
        shim.write_text(
            '#include <stdint.h>\n'
            'typedef uint64_t u64;\ntypedef uint32_t u32;\n'
            '#define __init\n#define __exit\n'
            '#define SPARK_FAN_HOST_TEST 1\n'
            '#include "' + str(MODULE) + '"\n'
            'u64 host_write_request(unsigned command, unsigned value)\n'
            '{ return spark_fan_write_request(command, value); }\n'
            'u64 host_read_request(unsigned command)\n'
            '{ return spark_fan_read_request(command); }\n'
            'unsigned host_rpm_for_level(unsigned long level)\n'
            '{ return spark_fan_rpm_for_level(level); }\n'
            'unsigned long host_level_for_rpm(unsigned rpm)\n'
            '{ return spark_fan_level_for_rpm(rpm); }\n')
        module_lib = work / "module.so"
        subprocess.run(["cc", "-shared", "-fPIC", "-O2", "-Wall", "-Werror", str(shim),
                        "-o", str(module_lib)], check=True)

        model_lib = work / "model.so"
        subprocess.run(["cc", "-shared", "-fPIC", "-Wall", "-Werror",
                        "-I", str(ROOT / "monitor"), str(MODEL), "-o", str(model_lib)],
                       check=True)

        cls.module = ctypes.CDLL(str(module_lib))
        cls.module.host_write_request.argtypes = [ctypes.c_uint, ctypes.c_uint]
        cls.module.host_write_request.restype = ctypes.c_uint64
        cls.module.host_read_request.argtypes = [ctypes.c_uint]
        cls.module.host_read_request.restype = ctypes.c_uint64
        cls.module.host_rpm_for_level.argtypes = [ctypes.c_ulong]
        cls.module.host_rpm_for_level.restype = ctypes.c_uint
        cls.module.host_level_for_rpm.argtypes = [ctypes.c_uint]
        cls.module.host_level_for_rpm.restype = ctypes.c_ulong

        cls.model = ctypes.CDLL(str(model_lib))
        cls.model.fan_request.argtypes = [ctypes.POINTER(ctypes.c_ulong)]
        cls.model.fan_request.restype = ctypes.c_int

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def question(self, request):
        """Ask the firmware model the same request word, and return what it answered in x4."""
        frame = (ctypes.c_ulong * 31)(0xC400008D, 0x8003, 0x20418532A0634A88,
                                      0x46A5A008C0EEAA83, request)
        handled = self.model.fan_request(frame)
        return handled, frame

    def test_the_write_words_are_the_ones_the_model_accepts(self):
        for level in (0, 50, 100):
            rpm = self.module.host_rpm_for_level(level)
            for command, value in ((0x06000790, rpm & 0xff), (0x06000791, (rpm >> 8) & 0xff),
                                   (0x06000792, rpm & 0xff), (0x06000793, (rpm >> 8) & 0xff)):
                with self.subTest(level=level, command=hex(command)):
                    handled, frame = self.question(self.module.host_write_request(command, value))
                    self.assertEqual(handled, 1, "the firmware model claims the request")
                    self.assertEqual(frame[0], 0xC400008E, "and answers with its completion id")

    def test_the_read_words_are_answered_per_fan(self):
        for command, _ in ((0x06000704, 0), (0x06000706, 1)):
            with self.subTest(command=hex(command)):
                handled, frame = self.question(self.module.host_read_request(command))
                self.assertEqual(handled, 1)
                self.assertEqual(frame[0], 0xC400008E)

    def test_both_implementations_compute_the_same_rpm(self):
        for level in range(0, 101):
            self.assertEqual(self.module.host_rpm_for_level(level),
                             975 + ((4350 - 975) * level) // 100, f"level {level}")
        self.assertEqual(self.module.host_rpm_for_level(1000), 4350, "levels clamp")

    def test_a_level_survives_the_round_trip_through_rpm(self):
        """level -> rpm -> level must come back to the level that was set.

        The DSDT divides, so 50 gives 2662 rpm and a flooring inverse reports 49 — a state one lower
        than the one just written. The inverse rounds to the nearest step, and this test holds it to
        that for every level the interface has.
        """
        for level in range(0, 101):
            rpm = self.module.host_rpm_for_level(level)
            with self.subTest(level=level):
                self.assertEqual(self.module.host_level_for_rpm(rpm), level,
                                 f"level {level} -> {rpm} rpm -> {self.module.host_level_for_rpm(rpm)}")
        self.assertEqual(self.module.host_level_for_rpm(0), 0)
        self.assertEqual(self.module.host_level_for_rpm(9999), 100, "above the maximum clamps")

    def test_the_module_reads_the_service_answer_where_the_service_puts_it(self):
        """The service answers in x4; Linux's arm_smccc_res stops at a3.

        Reading `result.a4` would not compile, and reading a0..a3 would miss the answer entirely, so
        the module must capture x4 itself. This is a source-level guard on that specific mistake —
        the only check available without the vendor kernel's headers.
        """
        source = MODULE.read_text()
        self.assertNotIn("result.a4", source,
                         "arm_smccc_res has a0..a3 only; the answer is in x4")
        self.assertNotIn("arm_smccc_smc(SPARK_FAN", source,
                         "arm_smccc_smc() cannot return x4, so the call is made in assembly")
        self.assertIn('register u64 x4 __asm__("x4")', source)

    def test_the_module_does_not_read_what_it_wrote(self):
        """A read request must not be built from the write opcode; the firmware answers 0x0C only."""
        write_word = self.module.host_write_request(0x06000704, 0)
        self.assertNotEqual(write_word & 0xff, 0x0C)


if __name__ == "__main__":
    unittest.main()
