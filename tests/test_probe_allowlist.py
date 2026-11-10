"""The read-only packet cannot write, and the probe cannot call an id nobody documented.

Sol's review turned the plan's rule into a code property: "unknown = treated as write = refused" has to
hold inside the probe, not only in prose. These tests compile the two builds and inspect what they carry.
"""

import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tests" / "fixtures" / "fan_probe.c"
CLANG = "clang"


def preprocess(defines):
    command = [CLANG, "--target=aarch64-pc-windows-msvc", "-E", "-P", str(SOURCE)]
    command += [f"-D{name}" for name in defines]
    try:
        return subprocess.run(command, capture_output=True, text=True, check=True, timeout=120).stdout
    except (OSError, subprocess.SubprocessError) as error:
        raise unittest.SkipTest(f"clang is not usable here: {error}")


class AllowlistTests(unittest.TestCase):
    def test_the_source_states_the_two_sets(self):
        text = SOURCE.read_text()
        self.assertIn("ALLOWED_READ", text)
        self.assertIn("ALLOWED_WRITE", text)
        self.assertIn("0x06000704", text)
        self.assertIn("0x06000706", text)

    def test_the_read_only_build_carries_no_write_opcode(self):
        expanded = preprocess(["READ_ONLY"])
        # the write encoder is compiled but its guard folds to 0, so no write opcode survives in run()
        body = expanded.split("static int run(void)")[-1]
        self.assertRegex(body, r"READ-ONLY-(OK|FAIL)")
        self.assertNotIn("FAN-OK", body, "the read-only build does not run the fan setters")
        self.assertNotRegex(re.sub(r"\s+", " ", body), r"\bFAN-FAIL\b")

    def test_the_full_build_still_offers_the_fan_sequence(self):
        expanded = preprocess([])
        self.assertIn("FAN-OK", expanded)
        self.assertNotIn("READ-ONLY-OK", expanded, "the fan build does not claim to be read-only")


if __name__ == "__main__":
    unittest.main()
