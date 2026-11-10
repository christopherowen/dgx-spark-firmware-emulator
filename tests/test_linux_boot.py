"""The emulator runs a whole Linux image, not just firmware.

The vendor's recovery media boots a kernel and an initramfs that run an installer in userspace.
`tools/run_linux.py` boots that pair under this QEMU build and records what happened; this test
asserts the milestone — the kernel comes up and its userspace starts — so a regression in the
machine, the kernel arguments or the harness fails here rather than in a hand-run.

Gated twice: the profile build and the vendor archive, neither of which a clean clone has.
"""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ARCHIVE = ROOT / "firmware" / "downloads" / "dgx-spark-recovery-1.155.49.tar.gz"
QEMU = ROOT / "build" / "qemu-spark" / "qemu-system-aarch64"


@unittest.skipUnless(QEMU.is_file(), "the profile is not built")
@unittest.skipUnless(ARCHIVE.is_file(), "the recovery archive is not downloaded")
class LinuxBootTests(unittest.TestCase):
    def test_the_recovery_environment_reaches_userspace(self):
        result = subprocess.run(
            [sys.executable, "tools/run_linux.py", "--recovery", str(ARCHIVE),
             "--timeout", "75"],
            cwd=ROOT, capture_output=True, text=True,
            env={**os.environ, "PATH": f"{ROOT / 'build' / 'host-tools' / 'bin'}:{os.environ['PATH']}"})
        self.assertEqual(result.returncode, 0, result.stderr[-300:])
        directory = Path(result.stdout.strip().splitlines()[-1])
        record = json.loads((directory / "run.json").read_text())
        self.assertEqual(record["result"], "userspace", record.get("lines"))
        self.assertTrue(record["reached_userspace"])
        self.assertEqual(record["kernel_version"], "7.0.0-1018-nvidia")
        self.assertEqual(record["image_version"], "1.155.49")
        # The installer prints its identity before it looks for its media; that banner is the proof
        # that the initramfs ran, not merely that the kernel decompressed.
        console = (directory / "console.log").read_text(errors="replace")
        self.assertIn("--- FastOS Version", console)
        self.assertIn("DGX Spark FASTOS", console)


if __name__ == "__main__":
    unittest.main()
