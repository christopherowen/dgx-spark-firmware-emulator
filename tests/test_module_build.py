"""Compile the module against a real kernel header tree, when one is available.

`tests/test_kernel_module_syntax.py` compiles against stubs, which catches a missing declaration but
proves nothing about the real headers. This test runs the real build when `SPARK_HEADERS` points at a
header tree copied from a unit — the path `kernel/README.md` documents — and requires an aarch64
relocatable with the vermagic it was asked for.
"""

import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEADERS = os.environ.get("SPARK_HEADERS")
VERMAGIC = os.environ.get("SPARK_VERMAGIC")


@unittest.skipUnless(HEADERS and VERMAGIC, "set SPARK_HEADERS and SPARK_VERMAGIC to build for real")
class ModuleBuildTests(unittest.TestCase):
    def test_the_module_compiles_against_the_units_headers(self):
        completed = subprocess.run(
            [sys.executable, "tools/build_module.py", "--headers", HEADERS, "--vermagic", VERMAGIC],
            cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, (completed.stdout + completed.stderr)[-800:])
        module = ROOT / "kernel" / "spark_fan.ko"
        self.assertTrue(module.is_file(), "the build produced no module")
        kind = subprocess.run(["file", "-b", str(module)], capture_output=True, text=True).stdout
        self.assertIn("ARM aarch64", kind, "the module is not an aarch64 object")
        details = subprocess.run(["modinfo", str(module)], capture_output=True, text=True,
                                 check=False).stdout
        if details:
            self.assertIn(VERMAGIC, details, "the module's vermagic is not the unit's")
            self.assertIn("allow_pin", details, "the opt-in parameter is not in the module")


if __name__ == "__main__":
    unittest.main()


class ModuleBuildDiagnosisTests(unittest.TestCase):
    """A failed kbuild should say *why* in terms of the target kernel, not "object not produced"."""

    def test_a_gcc_built_kernel_is_named_as_such(self):
        from tools.build_module import diagnose
        message = diagnose("clang: error: unknown argument: '-fconserve-stack'")
        self.assertIn("built with GCC", message)
        self.assertIn("make -C /lib/modules", message, "it must say where to build instead")

    def test_target_architecture_host_tools_are_named(self):
        from tools.build_module import diagnose
        message = diagnose("scripts/basic/fixdep: Exec format error")
        self.assertIn("target architecture", message)

    def test_an_unknown_failure_still_points_at_the_output(self):
        from tools.build_module import diagnose
        self.assertIn("run make by hand", diagnose("something else"))
