r"""Execute the vendor's own fan methods, rather than reading them.

The DSDT declares `\\_SB.FAN0._FSL` and `_FST`; `tests/fixtures/fan_aml_check.asl` is a small SSDT
that calls them. With ACPICA's `iasl` and `acpiexec` available and a captured DSDT in the run
directory, this evaluates `_FSL(level)` and reads `_FST` back: the Control field `_FST` returns is
the level the AML stored, which is what the kernel's `cur_state` shows and what the model in
`monitor/fan.c` reproduces.

The rpm itself is not observable this way — the SMC lives behind an FFixedHW region that ACPICA has
no handler for, so the writes are inert here. That is why the emulator run exists: it is the only
place the words the AML builds reach a service that answers them.
"""

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "fan_aml_check.asl"
RUNS = ROOT / "firmware" / "runs"


def tool(name):
    found = shutil.which(name)
    if not found:
        raise unittest.SkipTest(f"{name} is not installed")
    return found


def captured_dsdt():
    if not RUNS.is_dir():
        return None
    for directory in sorted(RUNS.iterdir(), reverse=True):
        for candidate in directory.glob("acpi-DSDT-*.bin"):
            return candidate
    return None


class FanAmlTests(unittest.TestCase):
    def setUp(self):
        self.iasl, self.acpiexec = tool("iasl"), tool("acpiexec")
        self.dsdt = captured_dsdt()
        if self.dsdt is None:
            self.skipTest("no captured DSDT under firmware/runs; run the census first")

    def test_the_vendor_methods_store_the_level_and_report_it(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "fan_check"
            compiled = subprocess.run([self.iasl, "-e", str(self.dsdt), "-p", str(output),
                                       str(FIXTURE)], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stdout[-400:] + compiled.stderr[-400:])
            for level in (0, 50, 100):
                with self.subTest(level=level):
                    result = subprocess.run(
                        [self.acpiexec, "-b", f"execute \\FSET {level}; execute \\FGET",
                         str(output.with_suffix(".aml")), str(self.dsdt)],
                        capture_output=True, text=True)
                    text = result.stdout
                    self.assertIn(f"Evaluating \\FSET", text)
                    # _FST returns { Revision, Control, Speed }; Control is the level just set.
                    package = re.search(r"\[Package\] Contains 3 Elements:(.*?)ACPI:",
                                        text, re.S)
                    self.assertIsNotNone(package, text[-400:])
                    values = [int(value, 16) for value in
                              re.findall(r"\[Integer\] = ([0-9A-F]+)", package.group(1))][:3]
                    self.assertEqual(values[1], level,
                                     f"_FST reports control {values[1]} for level {level}")


if __name__ == "__main__":
    unittest.main()
