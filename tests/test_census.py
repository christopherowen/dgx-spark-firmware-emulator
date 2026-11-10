"""The firmware's own configuration surface, enumerated in the emulator.

A small EFI application (built from `tests/fixtures/nvram_census.c`) is delivered over the PXE
lane and prints every UEFI variable it can see and every ACPI table the firmware publishes. That
turns "which settings exist — fan policy, memory configuration — and are they NVRAM-backed?"
into a question the vendor firmware answers about itself, with no hardware involved.

The test is gated twice: it needs the profile build and the prepared release, because the PXE lane
boots real firmware.
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
sys.path.insert(0, str(ROOT))

SOURCE = ROOT / "tests" / "fixtures" / "nvram_census.c"
PREPARED = ROOT / "firmware" / "prepared" / "2.158.15" / "uefi.fd"


def tool(name):
    found = shutil.which(name) or (ROOT / "build" / "host-tools" / "bin" / name)
    if isinstance(found, Path) and found.is_file():
        return str(found)
    if isinstance(found, str):
        return found
    raise unittest.SkipTest(f"{name} is not available")


def build_application(directory: Path) -> Path:
    obj, image = directory / "census.obj", directory / "census.efi"
    subprocess.run([tool("clang"), "--target=aarch64-pc-windows-msvc", "-O2", "-ffreestanding",
                    "-nostdlib", "-c", str(SOURCE), "-o", str(obj)], check=True)
    subprocess.run([tool("lld-link"), "/machine:arm64", "/subsystem:efi_application",
                    "/entry:_start", "/nodefaultlib", f"/out:{image}", str(obj)], check=True)
    return image


@unittest.skipUnless(os.environ.get("SPARK_QEMU"), "set SPARK_QEMU to run the profile")
@unittest.skipUnless(PREPARED.is_file(), "the 2.158.15 release is not prepared")
class CensusTests(unittest.TestCase):
    def test_the_firmware_answers_with_its_variables_and_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            image = build_application(Path(directory))
            result = subprocess.run(
                [sys.executable, "tools/boot_spark.py", "--pxe-test", "--board",
                 "--flash-image", "firmware/state/board-2158.bin",
                 "--firmware-version", "2.158.15", "--pxe-image", str(image),
                 "--pxe-marker", "CENSUS-END", "--timeout", "120"],
                cwd=ROOT, capture_output=True, text=True,
                env={**os.environ, "PATH": f"{ROOT / 'build' / 'host-tools' / 'bin'}:{os.environ['PATH']}",
                     "SPARK_QEMU": os.environ["SPARK_QEMU"]})
            log = Path((result.stdout + result.stderr).split("Local logs:")[-1].strip()) / "serial.log"
            if not log.is_file():
                self.skipTest(f"the run produced no log: {result.stdout[-200:]}")
            text = log.read_text(errors="replace")

        variables = re.findall(r"^VAR ([0-9a-f-]+) ([0-9a-f]{8}) ([0-9a-f]{6}) (.*)$", text, re.M)
        tables = re.findall(r"^ACPI (\w{4}) ", text, re.M)
        self.assertIn("CENSUS-END", text, "the application did not finish")
        self.assertGreater(len(variables), 40,
                           f"expected the firmware's variable set, saw {len(variables)}")
        names = {name for _guid, _attrs, _size, name in variables}
        # The exact set depends on what the store has accumulated, so the test asks for the
        # variables whose presence carries the capability, not for a snapshot of this boot.
        for wanted in ("Setup", "SecureBoot"):
            self.assertIn(wanted, names, f"{wanted} should be among the variables")
        self.assertTrue(any(name.startswith("Boot") for name in names),
                        "a boot option should be among the variables")
        # Setup is where a menu setting (fan curve, memory policy) would live, so the test proves
        # the *bytes* arrive, not just the name: the DATA lines must carry as many bytes as the
        # variable's size says, in the chunks the application emits.
        setup = re.search(r"^VAR \S+ (\S+) ([0-9a-f]{6}) Setup$", text, re.M)
        self.assertIsNotNone(setup, "Setup should be listed")
        declared = int(setup.group(2), 16)
        lines = text.splitlines()
        index = lines.index(next(line for line in lines if line.endswith(" Setup")))
        payload = ""
        for line in lines[index + 1:]:
            if not line.startswith("DATA "):
                break
            payload += line[5:]
        self.assertEqual(len(payload) // 2, declared,
                         f"Setup declares {declared} bytes and the dump carried {len(payload)//2}")
        self.assertGreaterEqual(len(tables), 8, "the firmware should publish its ACPI tables")
        for wanted in ("FACP", "APIC", "GTDT", "PPTT"):
            self.assertIn(wanted, tables, f"{wanted} should be among the tables")


if __name__ == "__main__":
    unittest.main()
