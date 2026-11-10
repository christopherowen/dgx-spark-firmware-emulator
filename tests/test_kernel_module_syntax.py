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

    def test_the_built_module_registers_the_opt_in_parameter(self):
        """Registration is a property of the artifact, so it is read from the artifact.

        `modinfo` parses the parameter table the linker emitted; a `module_param` line in the source
        that never reached the module would not appear here. What this still does not prove is that
        the refusal runs — that needs the module loaded, which needs a unit.
        """
        module = ROOT / "kernel" / "spark_fan.ko"
        if not module.exists():
            raise unittest.SkipTest("the module is a build product; build it with tools/build_module.py")
        modinfo = shutil.which("modinfo")
        if not modinfo:
            raise unittest.SkipTest("modinfo is required to read the parameter table")
        result = subprocess.run([modinfo, "-F", "parm", str(module)],
                                capture_output=True, text=True, check=True)
        self.assertIn("allow_pin:", result.stdout)
        self.assertIn("(bool)", result.stdout)

    def test_the_gate_survives_the_host_build_it_is_excluded_from(self):
        """Preprocess both builds: the gate must be in the kernel one and absent from the host one.

        An earlier version compared the positions of two strings, which a reordered but correct file
        fails and a broken nested condition passes. The preprocessor answers the real question.
        """
        compiler = shutil.which("cc")
        if not compiler:
            raise unittest.SkipTest("a C compiler is required")

        def preprocessed(*extra):
            return subprocess.run([compiler, "-E", "-P", "-I", str(STUBS), *extra, str(SOURCE)],
                                  capture_output=True, text=True, check=True).stdout

        host = preprocessed("-DSPARK_FAN_HOST_TEST")
        kernel = preprocessed()
        # The variables exist in both builds (the encoders share the file); the registration and the
        # refusal are the kernel half's, and that is what must not be silently excluded.
        # The stub headers expand both macros, so their expansions are what appear here — which is
        # the point of asking the preprocessor instead of searching source text.
        self.assertNotIn("__stub_param_allow_pin", host, "registration is kernel-only")
        self.assertNotIn("if (!allow_pin)", host, "and so is the gate")
        self.assertIn("__stub_param_allow_pin", kernel, "the parameter is registered in this half")
        self.assertIn("if (!allow_pin)", kernel)
        self.assertIn("return -1;", kernel, "EPERM expands to the refusal the caller sees")


if __name__ == "__main__":
    unittest.main()
