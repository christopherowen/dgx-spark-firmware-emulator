"""Run an original synthetic AArch64 probe against the built QEMU profile."""

import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.environ.get("SPARK_QEMU"), "set SPARK_QEMU to test the custom QEMU build")
class ProfileIntegrationTests(unittest.TestCase):
    def test_gpio_aliases_gicv3_id_and_16550_serial_at_spark_addresses(self):
        # Original probe: exercise GPIO readback, SET, and CLEAR aliases,
        # read GICD_PIDR2 at 0x0680ffe8, require architecture 3,
        # then write P (pass) or F (failure) to the 16550 at 0x16a00000.
        # These are our own assembled instructions, not vendor firmware bytes.
        words = [0xd29a6000, 0xf2a20040, 0x52824681, 0xb9000001,
                 0x52800021, 0xb9000401, 0x52800081, 0xb9000801,
                 0xb9400001, 0x52824622, 0x6b02003f, 0x54000161,
                 0xd29ffd00, 0xf2a0d000, 0xb9400001, 0x53041c21,
                 0x71000c3f, 0x540000a1, 0xd2a2d400, 0x52800a01,
                 0xb9000001, 0x14000000, 0xd2a2d400, 0x528008c1,
                 0xb9000001, 0x17fffffc]
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "original-probe.bin"
            image.write_bytes(struct.pack("<" + "I" * len(words), *words))
            command = [os.environ["SPARK_QEMU"], "-M",
                       "virt,spark-fw-lab=on,gic-version=3,virtualization=on",
                       "-cpu", "max", "-m", "2G", "-smp", "1", "-nodefaults",
                       "-display", "none", "-monitor", "none", "-serial", "stdio",
                       "-nic", "none", "-device",
                       f"loader,file={image},addr=0x80000000,cpu-num=0,force-raw=on"]
            try:
                result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, timeout=2, check=False)
                self.fail(f"probe unexpectedly exited: {result.stdout!r}")
            except subprocess.TimeoutExpired as result:
                self.assertEqual(result.output, b"P")
