"""A compile gate for the kernel-facing half of the module.

The host tests cover the encoders; they cannot see an identifier used but never declared, which is
exactly how `allow_pin` and `warned` shipped undeclared once. This compiles `kernel/spark_fan.c`
against a stub of the kernel API surface in `tests/kernel-stubs/` — a test double, not a kernel
header — with `-Werror`, so the whole file has to be syntactically valid and warning-free.

What it proves: the file parses, every name it uses is declared, and the types line up. What it does
not prove: anything about kernel behaviour or that the real headers declare the same signatures.
"""

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "kernel" / "spark_fan.c"
STUBS = ROOT / "tests" / "kernel-stubs"


class KernelModuleSyntaxTests(unittest.TestCase):
    def test_the_kernel_facing_half_is_declared_and_warning_free(self):
        compiler = shutil.which("cc")
        if not compiler:
            self.skipTest("a C compiler is required")
        result = subprocess.run([compiler, "-fsyntax-only", "-Wall", "-Wextra", "-Werror",
                                 "-I", str(STUBS), str(SOURCE)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr[-800:])

    def test_the_opt_in_parameter_is_declared_and_registered(self):
        """The gate must exist as code, not only in a comment: the declarations, the module_param
        registration and the refusal all have to be present in the source."""
        source = SOURCE.read_text()
        self.assertIn("static SPARK_FAN_KEEP bool allow_pin;", source)
        self.assertIn("static SPARK_FAN_KEEP bool warned;", source)
        self.assertIn("module_param(allow_pin, bool, 0444);", source)
        self.assertIn("return -EPERM;", source)

    def test_the_module_does_not_depend_on_the_host_test_define(self):
        """The kernel half is what runs on a unit, so it must not be inside the host-only branch."""
        source = SOURCE.read_text()
        guard = source.index("#ifndef SPARK_FAN_HOST_TEST")
        refusal = source.index("return -EPERM;")
        self.assertLess(guard, refusal, "the gate is inside the kernel half, after the guard")


if __name__ == "__main__":
    unittest.main()
