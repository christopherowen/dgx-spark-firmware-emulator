"""The device path a PXE-loaded image sees, and the two offsets that must be right to ask.

`EFI_SYSTEM_TABLE` puts `RuntimeServices` at 0x58 and `BootServices` at 0x60; 0x48 and 0x50 are the
standard-error handle and its text-output protocol, whose first field is a function pointer — so the wrong
offset still looks plausible and answers nonsense. `EFI_BOOT_SERVICES.HandleProtocol` is index 17 after the
24-byte header.

With those right, the probe reports what this firmware gives a PXE image: `EFI_LOADED_IMAGE_PROTOCOL` is
present, `HandleProtocol` for the device-path protocol on the image handle answers `EFI_UNSUPPORTED`, and
`FilePath` is a bare end-of-path node — the network path is not recorded. That is why a `Driver####`
variable cannot simply point at the file the probe was loaded from.
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
SOURCE = ROOT / "tests" / "fixtures" / "dp_self.c"
STATE = ROOT / "firmware" / "state" / "board-2158.bin"


def tool(name: str) -> str:
    found = shutil.which(name) or ROOT / "build" / "host-tools" / "bin" / name
    if isinstance(found, Path) and found.is_file():
        return str(found)
    if isinstance(found, str) and found:
        return found
    raise unittest.SkipTest(f"{name} is not available")


def build(directory: Path) -> Path:
    obj, image = directory / "dp_self.obj", directory / "dp_self.efi"
    subprocess.run([tool("clang"), "--target=aarch64-pc-windows-msvc", "-O2", "-ffreestanding",
                    "-nostdlib", "-c", str(SOURCE), "-o", str(obj)], check=True)
    subprocess.run([tool("lld-link"), "/machine:arm64", "/subsystem:efi_application",
                    "/entry:_start", "/nodefaultlib", f"/out:{image}", str(obj)], check=True)
    return image


class DevicePathSourceTests(unittest.TestCase):
    def test_the_probe_states_the_two_offsets_it_depends_on(self):
        """Execute the entry point: reject invalid tables before any Boot Services call."""
        with host_probe(SOURCE, "boot_services_harness.c", ("DEVICE_PATH",)) as invoke:
            for state in ("bad-signature", "no-boot"):
                result = invoke(state)
                self.assertEqual(result["status"], 1)
                self.assertEqual([result[key] for key in ("opens", "locates", "releases", "unexpected")],
                                 [0, 0, 0, 0], "invalid Boot Services must not be called")
                self.assertEqual(bytes.fromhex(result["output_hex"]), b"SELF-FAIL boot services\n")
            result = invoke("valid")
            self.assertEqual(result["status"], 0)
            self.assertEqual([result[key] for key in ("opens", "locates", "releases", "unexpected")],
                             [1, 0, 0, 0], "HandleProtocol must use the real Boot Services slot")
            self.assertEqual(bytes.fromhex(result["output_hex"]), b"SELF 4 7fff0400\nSELF-END\n")

    @unittest.skipUnless(os.environ.get("SPARK_QEMU"), "set SPARK_QEMU to run the profile")
    @unittest.skipUnless(STATE.is_file(), "a device image is needed to boot")
    def test_a_pxe_image_reports_an_empty_file_path(self):
        with tempfile.TemporaryDirectory() as directory:
            image = build(Path(directory))
            result = subprocess.run(
                [sys.executable, "tools/boot_spark.py", "--pxe-test", "--board",
                 "--flash-image", str(STATE), "--firmware-version", "2.158.15",
                 "--pxe-image", str(image), "--pxe-marker", "SELF-END", "--timeout", "150"],
                cwd=ROOT, capture_output=True, text=True,
                env={**os.environ, "PATH": f"/usr/lib/llvm-18/bin:{ROOT / 'build' / 'host-tools' / 'bin'}:{os.environ['PATH']}"})
            self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[-400:])
            log = Path((result.stdout + result.stderr).split("Local logs:")[-1].strip()) / "serial.log"
            text = log.read_text(errors="replace")
        found = re.search(r"^SELF (\d+) ([0-9a-f]+)$", text, re.M)
        self.assertIsNotNone(found, "the probe printed a path")
        length, raw = int(found.group(1)), found.group(2)
        self.assertEqual(len(raw), length * 2)
        self.assertEqual(raw, "7fff0400",
                         "a PXE image gets a bare end-of-path node: the network path is not recorded")


if __name__ == "__main__":
    unittest.main()
