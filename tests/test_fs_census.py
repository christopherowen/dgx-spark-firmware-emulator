"""What the firmware exposes to a boot option: device paths, and no file system at all.

The probe walks the handle database for device paths and asks each one for
`EFI_SIMPLE_FILE_SYSTEM_PROTOCOL`. On this firmware it finds 32 device paths and **zero** file systems,
so a `Driver####` entry cannot name a file on a volume — there is no volume to name. That is the negative
this test keeps: if the model ever starts publishing a file system, the assumption behind it changes and
the record should be revisited.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from host_probe import host_probe

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tests" / "fixtures" / "fs_census.c"
STATE = ROOT / "firmware" / "state" / "board-2158.bin"


def tool(name: str) -> str:
    found = shutil.which(name) or ROOT / "build" / "host-tools" / "bin" / name
    if isinstance(found, Path) and found.is_file():
        return str(found)
    if isinstance(found, str) and found:
        return found
    raise unittest.SkipTest(f"{name} is not available")


def build(directory: Path) -> Path:
    obj, image = directory / "fs_census.obj", directory / "fs_census.efi"
    subprocess.run([tool("clang"), "--target=aarch64-pc-windows-msvc", "-O2", "-ffreestanding",
                    "-nostdlib", "-c", str(SOURCE), "-o", str(obj)], check=True)
    subprocess.run([tool("lld-link"), "/machine:arm64", "/subsystem:efi_application",
                    "/entry:_start", "/nodefaultlib", f"/out:{image}", str(obj)], check=True)
    return image


class FileSystemCensusTests(unittest.TestCase):
    def test_the_probe_checks_the_boot_services_signature_first(self):
        """Execute the census: a wrong signature must reject all Boot Services entries."""
        with host_probe(SOURCE, "boot_services_harness.c") as invoke:
            for state in ("bad-signature", "no-boot"):
                result = invoke(state)
                self.assertEqual(result["status"], 1)
                self.assertEqual([result[key] for key in ("opens", "locates", "releases", "unexpected")],
                                 [0, 0, 0, 0], "invalid Boot Services must not be called")
                self.assertEqual(bytes.fromhex(result["output_hex"]), b"FS-FAIL boot services\n")
            result = invoke("valid")
            self.assertEqual(result["status"], 0)
            self.assertEqual([result[key] for key in ("opens", "locates", "releases", "unexpected")],
                             [4, 1, 1, 0])
            self.assertEqual(bytes.fromhex(result["output_hex"]),
                             b"FS-HANDLES 2\nDP 0 8 010104007fff0400\nFS 0 4 7fff0400\nFS-CNT 1\nFS-END\n")

    @unittest.skipUnless(os.environ.get("SPARK_QEMU"), "set SPARK_QEMU to run the profile")
    @unittest.skipUnless(STATE.is_file(), "a device image is needed to boot")
    def test_the_firmware_publishes_paths_but_no_file_system(self):
        with tempfile.TemporaryDirectory() as directory:
            image = build(Path(directory))
            result = subprocess.run(
                [sys.executable, "tools/boot_spark.py", "--pxe-test", "--board",
                 "--flash-image", str(STATE), "--firmware-version", "2.158.15",
                 "--pxe-image", str(image), "--pxe-marker", "FS-END", "--timeout", "150"],
                cwd=ROOT, capture_output=True, text=True,
                env={**os.environ, "PATH": f"/usr/lib/llvm-18/bin:{ROOT / 'build' / 'host-tools' / 'bin'}:{os.environ['PATH']}"})
            self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[-400:])
            log = Path((result.stdout + result.stderr).split("Local logs:")[-1].strip()) / "serial.log"
            text = log.read_text(errors="replace")
        handles = re.search(r"^FS-HANDLES (\d+)$", text, re.M)
        self.assertIsNotNone(handles, "the probe reported how many device-path handles exist")
        self.assertGreater(int(handles.group(1)), 0, "the firmware has device paths")
        found = re.search(r"^FS-CNT (\d+)$", text, re.M)
        self.assertEqual(int(found.group(1)), 0,
                         "and none of them carries a file system — no volume for a Driver#### to name")


if __name__ == "__main__":
    unittest.main()
