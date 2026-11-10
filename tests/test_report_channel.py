"""The report channel a headless probe writes its answer to.

A probe delivered as a boot option prints to a screen nobody is watching. `efi_var.h` gives it a
non-volatile variable instead, which Linux exposes as `/sys/firmware/efi/efivars/SparkProbe-<guid>`, so
the answer survives the boot and is read over SSH.

Three things are worth pinning, because getting any of them wrong makes the channel silently useless:
the name and GUID (the read path depends on both), the table offsets (a wrong SetVariable pointer is a
crash, and this project has already paid for one wrong table offset), and the fact that the channel is
opt-in so the rehearsed probe is unchanged without `-DREPORT_VAR`.
"""

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEADER = ROOT / "tests" / "fixtures" / "efi_var.h"
PROBES = (ROOT / "tests" / "fixtures" / "fan_probe.c", ROOT / "tests" / "fixtures" / "pl_probe.c")

EXPECTED_NAME = "SparkProbe"
EXPECTED_GUID = "53504152-4b50-524f-4245-010000000001"


class ReportChannelTests(unittest.TestCase):
    def setUp(self):
        self.source = HEADER.read_text()

    def test_the_read_path_is_what_the_record_says_it_is(self):
        self.assertIn('"SparkProbe"', self.source.replace("REPORT_NAME ", ""))
        self.assertIn("#define REPORT_NAME \"SparkProbe\"", self.source)
        self.assertIn("0x53504152u", self.source)
        self.assertIn("0x4b50u", self.source)
        self.assertIn("0x524fu", self.source)
        # The bytes that complete the GUID, in the order efivars prints them.
        self.assertIn("0x42, 0x45, 0x01, 0x00, 0x00, 0x00, 0x00, 0x01", self.source)
        self.assertIn(EXPECTED_GUID, self.source)
        self.assertIn(EXPECTED_NAME, self.source)

    def test_the_table_offsets_are_the_verified_ones(self):
        """RuntimeServices at 0x58 and SetVariable at its +0x58.

        The 24-byte table header puts GetTime at 0x18, so SetVariable (ninth pointer) is at 0x58. This
        project already shipped a payload with BootServices read from 0x50 instead of 0x60; the offset is
        pinned in a test so it is not re-derived from memory.
        """
        self.assertIn("system_table + 0x58", self.source)
        self.assertIn("report_runtime + 0x58", self.source)
        self.assertIn("24-byte table header", self.source)

    def test_the_channel_is_opt_in(self):
        for probe in PROBES:
            text = probe.read_text()
            self.assertIn("#ifdef REPORT_VAR", text, probe.name)
            self.assertIn("#include \"efi_var.h\"", text, probe.name)
            self.assertLess(text.index("#ifdef REPORT_VAR"), text.index("_start"),
                            f"{probe.name}: the include must precede the code that uses it")

    def test_the_probes_still_build_without_the_channel(self):
        compiler = shutil.which("clang")
        linker = shutil.which("lld-link")
        if not compiler or not linker:
            raise unittest.SkipTest("clang and lld-link are required to build an EFI application")
        with tempfile.TemporaryDirectory() as directory:
            for probe in PROBES:
                obj = Path(directory) / (probe.stem + ".o")
                efi = Path(directory) / (probe.stem + ".efi")
                subprocess.run([compiler, "--target=aarch64-pc-windows-msvc", "-O2",
                                "-ffreestanding", "-nostdlib", "-DREAD_ONLY",
                                "-I", str(probe.parent), "-c", str(probe), "-o", str(obj)],
                               check=True, capture_output=True)
                subprocess.run([linker, "/machine:arm64", "/subsystem:efi_application",
                                "/entry:_start", "/nodefaultlib", str(obj), f"/out:{efi}"],
                               check=True, capture_output=True)
                self.assertTrue(efi.exists())
                self.assertGreater(efi.stat().st_size, 1024)


if __name__ == "__main__":
    unittest.main()
