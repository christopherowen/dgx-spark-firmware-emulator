"""The profile boots the vendor's recovery installer to userspace.

This is the end-to-end claim of the Linux work: the vendor UEFI runs under this repository's monitor,
a loader starts the vendor kernel from an emulated NVMe volume, and the vendor initramfs runs
`/init` and prints the FastOS banner. The test reads the marker from the run's serial log; the
harness's own exit status is not used because its PXE packet proof can fail on a boot that takes a
different path than the DHCP capture expects.

A boot under TCG takes minutes of wall time, so this is gated on `SPARK_QEMU` and a prepared release,
and it is skipped when the host cannot fit the guest.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tests" / "fixtures" / "linux_loader.c"
PREPARED = ROOT / "firmware" / "prepared" / "2.158.15" / "uefi.fd"
STATE = ROOT / "firmware" / "state" / "board-2158.bin"
ESP = ROOT / "firmware" / "state" / "esp.img"
BANNER = "--- FastOS Version"


def tool(name):
    found = shutil.which(name) or ROOT / "build" / "host-tools" / "bin" / name
    path = Path(found)
    if not path.is_file():
        raise unittest.SkipTest(f"{name} is not available")
    return str(path)


@unittest.skipUnless(os.environ.get("SPARK_QEMU"), "set SPARK_QEMU to run the profile")
@unittest.skipUnless(PREPARED.is_file() and STATE.is_file() and ESP.is_file(),
                     "a prepared release, a device image and an ESP are needed")
class ProfileUserspaceTests(unittest.TestCase):
    def test_the_recovery_init_runs_and_prints_its_banner(self):
        with tempfile.TemporaryDirectory() as directory:
            obj = Path(directory) / "loader.obj"
            image = Path(directory) / "loader.efi"
            subprocess.run([tool("clang"), "--target=aarch64-pc-windows-msvc", "-O2",
                            "-ffreestanding", "-nostdlib", "-c", str(SOURCE), "-o", str(obj)],
                           check=True)
            subprocess.run([tool("lld-link"), "/machine:arm64", "/subsystem:efi_application",
                            "/entry:_start", "/nodefaultlib", f"/out:{image}", str(obj)], check=True)
            result = subprocess.run(
                [sys.executable, "tools/boot_spark.py", "--pxe-test", "--board",
                 "--flash-image", str(STATE), "--firmware-version", "2.158.15",
                 "--pxe-image", str(image), "--pxe-marker", BANNER,
                 "--nvme-image", str(ESP), "--memory", "3G", "--timeout", "900"],
                cwd=ROOT, capture_output=True, text=True,
                env={**os.environ,
                     "PATH": f"{ROOT / 'build' / 'host-tools' / 'bin'}:{os.environ['PATH']}"})
            output = result.stdout + result.stderr
            if "available on this host" in output:
                self.skipTest(output.strip().splitlines()[0])
            log_line = [line for line in output.splitlines() if line.startswith("Local logs:")]
            self.assertTrue(log_line, output[-500:])
            text = Path(log_line[0].split(":", 1)[1].strip(), "serial.log").read_text(errors="replace")

        self.assertIn("Run /init as init process", text)
        self.assertIn(BANNER, text)
        self.assertRegex(text, r'VERSION="1\.155\.49"')
        self.assertIn("probe of PNP0C0B:00 returned 0", text,
                      "the ACPI fan devices bound on the way through")


if __name__ == "__main__":
    unittest.main()
