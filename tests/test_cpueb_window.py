r"""The block the firmware declares for its power and performance interface.

`\\_SB.MTEL` publishes `Memory32Fixed(0x05170000, 0x00060000)`, and a kernel's ACPICA system-memory
handler reads the processor `_CPC` registers inside it during boot — an address the machine does not
back is an external abort that kills the boot. The profile therefore models the block; this probe
writes and reads back a word at its base and at the far end where `_CPC` lives, so a change that
removes the model fails here rather than in a kernel trace.

The probe is our own instructions, assembled from `tests/fixtures/cpueb_probe.c`.
"""

import os
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

CPUEB_WORDS = [
    # movz x1, #0x517, lsl #16 ; movz w2, #0x3344 ; movk w2, #0x1122, lsl #16
    0xd2a0a2e1, 0x52866882, 0x72a22442,
    # str w2, [x1, #0x100] ; ldr w3, [x1, #0x100] ; cmp w3, w2 ; b.ne fail
    0xb9010022, 0xb9410023, 0x6b02007f, 0x54000181,
    # movz x6, #0xc404 ; movk x6, #0x5, lsl #16 ; add x6, x1, x6   (base + 0x5C404)
    0xd2988086, 0xf2a000a6, 0x8b060026,
    # str w2, [x6] ; ldr w3, [x6] ; cmp w3, w2 ; b.ne fail
    0xb90000c2, 0xb94000c3, 0x6b02007f, 0x540000a1,
    # movz x4, #0x16a0, lsl #16 ; mov w5, #'P' ; str w5, [x4] ; b end
    0xd2a2d404, 0x52800a05, 0xb9000085, 0x14000004,
    # fail: movz x4, #0x16a0, lsl #16 ; mov w5, #'F' ; str w5, [x4]
    0xd2a2d404, 0x528008c5, 0xb9000085,
    # end: b end
    0x14000000,
]


@unittest.skipUnless(os.environ.get("SPARK_QEMU"), "set SPARK_QEMU to test the custom QEMU build")
class CpuebWindowTests(unittest.TestCase):
    def test_the_declared_block_is_readable_and_writable(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "cpueb-probe.bin"
            image.write_bytes(struct.pack("<" + "I" * len(CPUEB_WORDS), *CPUEB_WORDS))
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
                self.assertEqual(timed_out.output, b"P",
                                 f"the probe printed {timed_out.output!r}")
                return
            message = (result.stdout or b"").decode("utf-8", "replace")
            if "Cannot allocate memory" in message or "failed to allocate" in message:
                self.skipTest("the host could not allocate the machine's memory")
            self.fail(f"probe unexpectedly exited: {result.stdout!r}")


if __name__ == "__main__":
    unittest.main()
