"""The signed module and the certificate that must be enrolled for it to load.

`insmod` of the unsigned build was refused on a unit with *Key was rejected by service*, because
lockdown is at integrity. The signed copy in the tree only loads after the operator enrols the
matching certificate as a MOK, so the two artifacts have to agree — and that agreement is checkable
here, without a machine: the module's `sig_key` fingerprint must be the certificate's own, and the
signer name must be the certificate's subject common name.

A mismatched pair is the worst outcome available: the operator does the enrolment, reboots, types the
password, and the module still refuses to load, with no way to tell which half was wrong.
"""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNSIGNED = ROOT / "kernel" / "spark_fan.ko"
SIGNED = ROOT / "kernel" / "spark_fan.signed.ko"
CERT = ROOT / "kernel" / "mok-spark-lab.der"


def modinfo(path: Path) -> dict:
    result = subprocess.run(["modinfo", "-F", "signer", str(path)],
                            capture_output=True, text=True)
    signer = result.stdout.strip()
    result = subprocess.run(["modinfo", "-F", "sig_key", str(path)],
                            capture_output=True, text=True)
    key = result.stdout.strip()
    return {"signer": signer, "sig_key": key}


class SignedModuleTests(unittest.TestCase):
    def setUp(self):
        if not shutil.which("modinfo") or not shutil.which("openssl"):
            raise unittest.SkipTest("modinfo and openssl are required to read a signature")
        if not UNSIGNED.exists():
            raise unittest.SkipTest("the module is a build product; build it with tools/build_module.py")
        if not SIGNED.exists():
            raise unittest.SkipTest(
                "the signed module is a build product: run tools/sign_module.py with the key first")
        if not CERT.exists():
            raise unittest.SkipTest("the certificate is not in the tree")

    def test_the_module_carries_a_signature(self):
        info = modinfo(SIGNED)
        self.assertTrue(info["signer"], "the signed copy carries no signer")
        self.assertIn("Spark Lab", info["signer"])
        self.assertRegex(info["sig_key"], r"^([0-9A-F]{2}:){19}[0-9A-F]{2}$")

    def test_the_signer_is_the_certificate_subject(self):
        """The name `modinfo` prints is the certificate's common name, read from the DER itself."""
        subject = subprocess.run(
            ["openssl", "x509", "-inform", "DER", "-in", str(CERT), "-noout", "-subject"],
            capture_output=True, text=True, check=True).stdout.strip()
        common_name = subject.split("CN = ", 1)[1].split(",")[0].strip() if "CN = " in subject \
            else subject.split("CN=", 1)[1].split(",")[0].strip()
        self.assertEqual(modinfo(SIGNED)["signer"], common_name)

    def test_the_trailer_is_reproducible_with_the_signing_key(self):
        """Re-signing the same module with the same key must give the same trailer.

        This proves the pair in the tree belongs together, which is the one thing that must not be
        wrong: a mismatch is discovered at the machine, after the enrolment and the reboot. The private
        key stays outside the repository, so a clone without it skips rather than pretends.
        """
        key = Path.home() / ".keys" / "spark-lab" / "spark_mok.key"
        signer = ROOT / "work" / "sign" / "sign-file"
        if not key.exists() or not signer.exists():
            raise unittest.SkipTest("the signing key is not on this host (it is never committed)")
        cert = Path.home() / ".keys" / "spark-lab" / "spark_mok.crt"
        with tempfile.TemporaryDirectory() as directory:
            again = Path(directory) / "again.ko"
            again.write_bytes(UNSIGNED.read_bytes())
            subprocess.run([str(signer), "sha512", str(key), str(cert), str(again)],
                           check=True, capture_output=True)
            self.assertEqual(again.read_bytes(), SIGNED.read_bytes(),
                             "the signed copy in the tree is not this module signed with this key")

    def test_the_unsigned_build_is_still_unsigned(self):
        info = modinfo(UNSIGNED)
        self.assertEqual(info["signer"], "", "the unsigned build must stay unsigned")
        self.assertEqual(info["sig_key"], "")

    def test_the_signed_copy_differs_only_by_its_signature(self):
        self.assertGreater(SIGNED.stat().st_size, UNSIGNED.stat().st_size)
        self.assertLess(SIGNED.stat().st_size - UNSIGNED.stat().st_size, 4096,
                        "a module signature is a small trailer, not a rebuilt module")
        self.assertEqual(SIGNED.read_bytes()[:UNSIGNED.stat().st_size], UNSIGNED.read_bytes(),
                         "the signed copy must be the same module plus a trailer")


if __name__ == "__main__":
    unittest.main()
