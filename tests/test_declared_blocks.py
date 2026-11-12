"""The blocks the firmware declares, and a console window wider than the 16550 model.

A kernel reads these through ACPICA's system-memory handler and through the MediaTek 8250 driver;
an address the machine does not back is an external abort that kills the boot. The probe is our own
instructions, assembled from `tests/fixtures/declared_probe.c`, and it checks the seeded thermal
value, a vendor 8250 register beyond the eight a 16550 models, and MTEL's two small windows.
"""

import os
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

DECLARED_WORDS = [
    0xd2a38461, 0xf2900001,              # movz/movk x1, 0x1C238000
    0xb9481822,                          # ldr w2, [x1, #0x818]   (thermal zone register)
    0x52818da3, 0x6b03005f, 0x54000261,  # movz w3, #3181 ; cmp w2, w3 ; b.ne fail
    0xd2a2d401, 0x528b4b42,              # x1 = 0x16a00000 ; w2 = 0x5a5a
    0xb9020022, 0xb9420023, 0x6b02007f, 0x540001a1,   # store/load at +0x200, compare, b.ne fail
    0xd2a0a2a1, 0xb9448022, 0xb9450023,  # x1 = 0x05150000 ; ldr at +0x480 and +0x500
    0xd2a390e1, 0xf2804001, 0xb9400022,  # x1 = 0x1C870200 ; ldr w2, [x1]
    0xd2a62381, 0xb9400023,              # x1 = 0x311C0000 ; ldr w3, [x1]
    0xd2a2d404, 0x52800a05, 0xb9000085, 0x14000004,   # print 'P', skip the failure path
    0xd2a2d404, 0x528008c5, 0xb9000085,  # fail: print 'F'
    0x14000000,                          # b .
]


# The same probe compiled against a value that cannot match, so its failure branch is executed
# instead of assumed: it must print 'F' on this machine.
DECLARED_NEGATIVE_WORDS = [
    0xd2a38461, 0xf2900001, 0xb9481822, 0x5284e1e3, 0x6b03005f, 0x54000261,
    0xd2a2d401, 0x528b4b42, 0xb9020022, 0xb9420023, 0x6b02007f, 0x540001a1,
    0xd2a0a2a1, 0xb9448022, 0xb9450023, 0xd2a390e1, 0xf2804001, 0xb9400022,
    0xd2a62381, 0xb9400023, 0xd2a2d404, 0x52800a05, 0xb9000085, 0x14000004,
    0xd2a2d404, 0x528008c5, 0xb9000085, 0x14000000,
]


@unittest.skipUnless(os.environ.get("SPARK_QEMU"), "set SPARK_QEMU to test the custom QEMU build")
class DeclaredBlockTests(unittest.TestCase):
    def probe(self, words, expected):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "declared-probe.bin"
            image.write_bytes(struct.pack("<" + "I" * len(words), *words))
            command = [os.environ["SPARK_QEMU"],
                       "-M", "virt,spark-fw-lab=on,gic-version=3,virtualization=on",
                       "-cpu", "max", "-m", "2G", "-smp", "1", "-nodefaults",
                       "-display", "none", "-monitor", "none", "-serial", "stdio",
                       "-nic", "none", "-device",
                       f"loader,file={image},addr=0x80000000,cpu-num=0,force-raw=on"]
            try:
                result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, timeout=2, check=False)
            except subprocess.TimeoutExpired as timed_out:
                self.assertEqual(timed_out.output, expected.encode(),
                                 f"the probe printed {timed_out.output!r}")
                return
            message = (result.stdout or b"").decode("utf-8", "replace")
            if "Cannot allocate memory" in message or "failed to allocate" in message:
                self.skipTest("the host could not allocate the machine's memory")
            self.fail(f"probe unexpectedly exited: {result.stdout!r}")

    def test_the_failure_branch_reports_failure(self):
        self.probe(DECLARED_NEGATIVE_WORDS, "F")

    def test_declared_blocks_answer_and_the_console_window_is_wide(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "declared-probe.bin"
            image.write_bytes(struct.pack("<" + "I" * len(DECLARED_WORDS), *DECLARED_WORDS))
            command = [os.environ["SPARK_QEMU"],
                       "-M", "virt,spark-fw-lab=on,gic-version=3,virtualization=on",
                       "-cpu", "max", "-m", "2G", "-smp", "1", "-nodefaults",
                       "-display", "none", "-monitor", "none", "-serial", "stdio",
                       "-nic", "none", "-device",
                       f"loader,file={image},addr=0x80000000,cpu-num=0,force-raw=on"]
            try:
                result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, timeout=2, check=False)
            except subprocess.TimeoutExpired as timed_out:
                self.assertEqual(timed_out.output, b"P", f"the probe printed {timed_out.output!r}")
                return
            message = (result.stdout or b"").decode("utf-8", "replace")
            if "Cannot allocate memory" in message or "failed to allocate" in message:
                self.skipTest("the host could not allocate the machine's memory")
            self.fail(f"probe unexpectedly exited: {result.stdout!r}")


if __name__ == "__main__":
    unittest.main()
