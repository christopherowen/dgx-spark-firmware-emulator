"""No test may take its evidence from an ephemeral path.

Three tests in this suite read `/tmp/dsdt.dsl` — a decompiled copy that only the machine that made it
has. Where it was missing they skipped, which reads as "nothing to check" rather than "this claim is
unverified", and in CI they proved nothing at all. They now read the vendor table from
`firmware/downloads/`, which is ignored but durable.

The remaining `/tmp/` paths are vendor binaries that must not be committed, so their tests are right to
skip; they are listed here explicitly.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
# Vendor media that cannot live in the tree: a whole-part image extracted from the recovery archive.
ALLOWED = ("/tmp/usbimg.customer/",)


class EphemeralSourceTests(unittest.TestCase):
    def test_no_test_points_at_an_ephemeral_source(self):
        offenders = []
        for path in sorted(TESTS.glob("*.py")):
            if path.name == Path(__file__).name:
                continue                    # the guard has to name the pattern it forbids
            for number, line in enumerate(path.read_text().splitlines(), start=1):
                for found in re.findall(r'"[^"]*/tmp/[^"]*"', line):
                    literal = found.strip('"')
                    if any(literal.startswith(prefix) for prefix in ALLOWED):
                        continue
                    offenders.append(f"{path.name}:{number}: {literal}")
        self.assertEqual(offenders, [], "these tests would skip wherever the file is absent:\n  "
                                       + "\n  ".join(offenders))


if __name__ == "__main__":
    unittest.main()
