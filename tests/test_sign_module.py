"""The signing script: it signs what it is given, refuses what it must, and leaves the input alone.

A signed module is the only way through lockdown at `integrity`, and the failure mode that matters is
silent: a trailer over the wrong bytes, or an in-place edit that destroys the unsigned artifact the
operator needs if the enrolment does not work. Both are cheap to check here.
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "kernel" / "spark_fan.ko"
SIGNER = ROOT / "work" / "sign" / "sign-file"


class SignModuleTests(unittest.TestCase):
    def setUp(self):
        if not MODULE.exists():
            raise unittest.SkipTest("the module is a build product; build it with tools/build_module.py")
        if not SIGNER.exists():
            raise unittest.SkipTest("the kernel's sign-file is not built on this host")
        self.key = Path.home() / ".keys" / "spark-lab" / "spark_mok.key"
        self.cert = Path.home() / ".keys" / "spark-lab" / "spark_mok.crt"
        if not self.key.exists() or not self.cert.exists():
            raise unittest.SkipTest("the signing key is not on this host (it is never committed)")

    def run_script(self, *arguments):
        return subprocess.run([sys.executable, str(ROOT / "tools" / "sign_module.py"), *arguments],
                              capture_output=True, text=True)

    def test_it_signs_a_copy_and_leaves_the_unsigned_module_alone(self):
        before = MODULE.read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "signed.ko"
            result = self.run_script("--key", str(self.key), "--cert", str(self.cert),
                                     "--out", str(out))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(out.exists())
            self.assertEqual(out.read_bytes()[:len(before)], before,
                             "the signed copy must be the same module plus a trailer")
            self.assertGreater(out.stat().st_size, len(before))
        self.assertEqual(MODULE.read_bytes(), before, "the unsigned build must not be modified")

    def test_it_refuses_to_sign_in_place(self):
        result = self.run_script("--key", str(self.key), "--cert", str(self.cert),
                                 "--out", str(MODULE))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("in place", result.stderr)

    def test_it_refuses_a_missing_key(self):
        missing = Path(tempfile.gettempdir()) / "no-such-key-please.pem"
        result = self.run_script("--key", str(missing), "--cert", str(self.cert),
                                 "--out", str(Path(tempfile.gettempdir()) / "never.ko"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("private key not found", result.stderr)


if __name__ == "__main__":
    unittest.main()
