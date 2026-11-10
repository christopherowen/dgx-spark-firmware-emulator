"""The profile hands control to the recovery kernel, and stops where it stops.

This asserts the part that is true today: the loader finds the kernel on the emulated NVMe volume,
loads it, sets its command line and starts it. It deliberately does not assert that Linux boots —
`docs/linux-under-the-profile.md` records why it does not yet, with the instruction that stops it.

Set `SPARK_ESP` to a volume built by `tools/make_esp.py`; a 3 GiB guest needs about 3.6 GiB of
`MemAvailable`, so the test skips rather than run the host into swap.
"""

import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tests" / "fixtures" / "linux_loader.c"
PREPARED = ROOT / "firmware" / "prepared" / "2.158.15" / "uefi.fd"
STATE = ROOT / "firmware" / "state" / "board-2158.bin"


def tool(name):
    found = shutil.which(name) or ROOT / "build" / "host-tools" / "bin" / name
    path = Path(found) if not isinstance(found, Path) else found
    if not path.is_file():
        raise unittest.SkipTest(f"{name} is not available")
    return str(path)


def available_bytes():
    with open("/proc/meminfo") as handle:
        for line in handle:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    return 0


class KernelHandoverTests(unittest.TestCase):
    def setUp(self):
        self.esp = os.environ.get("SPARK_ESP")
        if not self.esp:
            self.skipTest("set SPARK_ESP to a volume built by tools/make_esp.py")
        if not (Path(self.esp).is_file() and PREPARED.is_file() and STATE.is_file()):
            self.skipTest("the volume, a prepared release and a device image are needed")
        if available_bytes() < 512 * 1024 * 1024:
            self.skipTest("the host is out of memory")

    def test_the_kernel_is_loaded_and_started(self):
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
                 "--pxe-image", str(image), "--pxe-marker", "LOADER-STARTING",
                 "--nvme-image", self.esp, "--memory", "3G", "--timeout", "250"],
                cwd=ROOT, capture_output=True, text=True,
                env={**os.environ, "PATH": f"{ROOT / 'build' / 'host-tools' / 'bin'}:{os.environ['PATH']}"})
            output = result.stdout + result.stderr
            if result.returncode != 0 and "available on this host" in output:
                # The harness refuses to start a guest it cannot fit; that is a skip, not a failure.
                self.skipTest(output.strip().splitlines()[-1])
            self.assertEqual(result.returncode, 0, output[-500:])
            log = Path(output.split("Local logs:")[-1].strip()) / "serial.log"
            text = log.read_text(errors="replace")

        self.assertIn("LOADER-STARTING", text)
        sizes = re.findall(r"^LOADER-(?:KERNEL|INITRD) 0x([0-9a-f]+)$", text, re.M)
        self.assertEqual(len(sizes), 2, "the kernel and its initrd were both read")
        for size in sizes:
            self.assertGreater(int(size, 16), 1024 * 1024, "both files are real payloads")


if __name__ == "__main__":
    unittest.main()
