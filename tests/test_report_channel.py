"""The report channel, executed rather than read.

A probe delivered as a boot option prints to a screen nobody is watching, so `efi_var.h` writes its
answer to a non-volatile variable that Linux exposes under `/sys/firmware/efi/efivars`. Three things
about that header are load-bearing and none of them can be established by grepping it:

* the name and GUID are the read path — a typo means the file is never found;
* the table offsets are a crash if wrong, and this project has already shipped one wrong table offset;
* the channel is opt-in, so the rehearsed payload is unchanged without `-DREPORT_VAR`.

`tests/fixtures/efi_var_harness.c` compiles the real header for the host and hands it a synthetic
system table: a canary function at the runtime slot's `+0x50`, the recording function at `+0x58`. If
the header read the wrong slot the harness prints `CANARY`, and `test_the_offset_is_the_one_it_claims`
proves it does by poisoning a copy of the header.
"""

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEADER = ROOT / "tests" / "fixtures" / "efi_var.h"
HARNESS = ROOT / "tests" / "fixtures" / "efi_var_harness.c"
PROBES = (ROOT / "tests" / "fixtures" / "fan_probe.c", ROOT / "tests" / "fixtures" / "pl_probe.c")

EXPECTED_NAME_UTF16 = "".join(f"{ord(c):04x}" for c in "SparkProbe")
EXPECTED_GUID = "53504152-4b50-524f-4245010000000001"
EXPECTED_ATTRIBUTES = 7


def compiler() -> str:
    found = shutil.which("cc")
    if not found:
        raise unittest.SkipTest("a C compiler is required")
    return found


def run_harness(header: Path = HEADER) -> str:
    """Compile the harness beside the given header, so the quote-include picks that copy up.

    A `#include "efi_var.h"` searches the including file's own directory first, so pointing `-I` at a
    poisoned copy is not enough — the harness has to be copied next to it.
    """
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        (work / "efi_var.h").write_text(header.read_text())
        harness = work / HARNESS.name
        harness.write_text(HARNESS.read_text())
        binary = work / "harness"
        subprocess.run([compiler(), "-O1", "-Wall", "-Wextra", str(harness), "-o", str(binary)],
                       check=True, capture_output=True)
        # A failure is reported on stdout, not by crashing: the caller asserts on the text.
        return subprocess.run([str(binary)], capture_output=True, text=True).stdout


class ReportChannelTests(unittest.TestCase):
    def test_the_channel_stores_what_the_read_side_expects(self):
        line = run_harness().strip()
        self.assertNotIn("CANARY", line, "the header called the function at +0x50, not +0x58")
        match = re.match(
            r"name=(?P<name>[0-9a-f]+) guid=(?P<guid>[0-9a-f-]+) attributes=(?P<attributes>\d+) "
            r"size=(?P<size>\d+) data=(?P<data>[\s\S]*)$", line)
        self.assertIsNotNone(match, line)
        self.assertEqual(match["name"], EXPECTED_NAME_UTF16)
        self.assertEqual(match["guid"], EXPECTED_GUID)
        self.assertEqual(match["attributes"], str(EXPECTED_ATTRIBUTES))
        self.assertEqual(match["data"].strip(), "R0 1978\n4096\n0x00000000deadbeef",
                         "the payload is exactly what the probe printed")

    def poison(self, old: str, new: str) -> str:
        """Compile the harness against a header whose one offset has been moved."""
        with tempfile.TemporaryDirectory() as directory:
            source = HEADER.read_text()
            self.assertIn(old, source)
            poisoned = Path(directory) / "efi_var.h"
            poisoned.write_text(source.replace(old, new))
            return run_harness(poisoned).strip()

    def test_the_set_variable_slot_is_the_one_it_claims(self):
        """Move SetVariable's slot by one pointer and the harness must call the canary instead."""
        self.assertEqual(self.poison("report_runtime + 0x58", "report_runtime + 0x50"), "CANARY")

    def test_the_runtime_services_slot_is_the_one_it_claims(self):
        """Move the runtime table's slot and there is no table at all: the init must fail, not crash."""
        self.assertEqual(self.poison("system_table + 0x58", "system_table + 0x50"), "INIT-FAILED")

    def test_the_channel_is_opt_in(self):
        """Without -DREPORT_VAR the probe must not carry the variable's name at all."""
        linker = shutil.which("lld-link")
        clang = shutil.which("clang")
        if not clang or not linker:
            raise unittest.SkipTest("clang and lld-link are required to build an EFI application")
        needle = "SparkProbe".encode("utf-16-le")
        with tempfile.TemporaryDirectory() as directory:
            for define, expected in (("-DREPORT_VAR", True), ("", False)):
                probe = PROBES[0]
                obj = Path(directory) / "p.o"
                efi = Path(directory) / "p.efi"
                command = [clang, "--target=aarch64-pc-windows-msvc", "-O2", "-ffreestanding",
                           "-nostdlib", "-DREAD_ONLY"]
                if define:
                    command.append(define)
                subprocess.run([*command, "-I", str(probe.parent), "-c", str(probe), "-o", str(obj)],
                               check=True, capture_output=True)
                subprocess.run([linker, "/machine:arm64", "/subsystem:efi_application",
                                "/entry:_start", "/nodefaultlib", str(obj), f"/out:{efi}"],
                               check=True, capture_output=True)
                present = needle in efi.read_bytes()
                self.assertEqual(present, expected,
                                 f"{'with' if expected else 'without'} -DREPORT_VAR the image must "
                                 f"{'contain' if expected else 'not contain'} the variable's name")

    def test_the_probes_still_build_without_the_channel(self):
        compiler_bin = shutil.which("clang")
        linker = shutil.which("lld-link")
        if not compiler_bin or not linker:
            raise unittest.SkipTest("clang and lld-link are required to build an EFI application")
        with tempfile.TemporaryDirectory() as directory:
            for probe in PROBES:
                obj = Path(directory) / (probe.stem + ".o")
                efi = Path(directory) / (probe.stem + ".efi")
                subprocess.run([compiler_bin, "--target=aarch64-pc-windows-msvc", "-O2",
                                "-ffreestanding", "-nostdlib", "-DREAD_ONLY",
                                "-I", str(probe.parent), "-c", str(probe), "-o", str(obj)],
                               check=True, capture_output=True)
                subprocess.run([linker, "/machine:arm64", "/subsystem:efi_application",
                                "/entry:_start", "/nodefaultlib", str(obj), f"/out:{efi}"],
                               check=True, capture_output=True)
                self.assertGreater(efi.stat().st_size, 1024)


if __name__ == "__main__":
    unittest.main()
