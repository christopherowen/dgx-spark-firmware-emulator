"""The power-limit window: raise a limit, see it, put it back — in the emulator.

`tests/fixtures/pl_window_probe.c` writes the OS PL1 slot through the served window and reads the
effective value before, after and after restoring. The offsets are the firmware's own, taken from
its interface table; the service is a lab GUID on the OEM function id the firmware already forwards,
because its own SMC filter drops ids it does not know.
"""

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tests" / "fixtures" / "pl_window_probe.c"
PREPARED = ROOT / "firmware" / "prepared" / "2.158.15" / "uefi.fd"
STATE = ROOT / "firmware" / "state" / "board-2158.bin"


def tool(name):
    from shutil import which
    found = which(name) or (ROOT / "build" / "host-tools" / "bin" / name)
    if isinstance(found, Path) and found.is_file():
        return str(found)
    if isinstance(found, str) and found:
        return found
    raise unittest.SkipTest(f"{name} is not available")


@unittest.skipUnless(os.environ.get("SPARK_QEMU"), "set SPARK_QEMU to run the profile")
@unittest.skipUnless(PREPARED.is_file() and STATE.is_file(), "the release and a device image are needed")
class PowerLimitModelTests(unittest.TestCase):
    def test_a_limit_can_be_raised_observed_and_restored(self):
        with tempfile.TemporaryDirectory() as directory:
            obj, image = Path(directory) / "pl.obj", Path(directory) / "pl.efi"
            subprocess.run([tool("clang"), "--target=aarch64-pc-windows-msvc", "-O2",
                            "-ffreestanding", "-nostdlib", "-c", str(SOURCE), "-o", str(obj)],
                           check=True)
            subprocess.run([tool("lld-link"), "/machine:arm64", "/subsystem:efi_application",
                            "/entry:_start", "/nodefaultlib", f"/out:{image}", str(obj)], check=True)
            result = subprocess.run(
                [sys.executable, "tools/boot_spark.py", "--pxe-test", "--board",
                 "--flash-image", str(STATE), "--firmware-version", "2.158.15",
                 "--pxe-image", str(image), "--pxe-marker", "PL-OK", "--timeout", "120"],
                cwd=ROOT, capture_output=True, text=True,
                env={**os.environ, "PATH": f"{ROOT / 'build' / 'host-tools' / 'bin'}:{os.environ['PATH']}"})
            self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[-400:])
            log = Path((result.stdout + result.stderr).split("Local logs:")[-1].strip()) / "serial.log"
            text = log.read_text(errors="replace")

        self.assertIn("PL-OK", text)
        before, os_slot, after, restored = (int(value) for value in
                                            re.findall(r"^[BOAR] (\d+)$", text, re.M)[:4])
        self.assertEqual(before, 0, "the effective value starts unset")
        self.assertEqual(os_slot, 45000, "the OS slot took the write")
        self.assertEqual(after, 45000, "the effective value followed it")
        self.assertEqual(restored, before, "and restoring puts it back")


if __name__ == "__main__":
    unittest.main()
