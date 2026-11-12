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


class ConsoleScriptTests(unittest.TestCase):
    """The guest console writer, tested without a guest.

    Two defects lived here and both hid the run's output instead of producing it: the thread closed
    the stream that `Popen.communicate()` flushes afterwards, and an unguarded write raised from a
    background thread. Neither can be observed from a boot test that only asserts a milestone, so
    they are pinned directly.
    """

    def setUp(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("run_linux_module",
                                                     ROOT / "tools" / "run_linux.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        import tempfile
        self.directory = Path(tempfile.mkdtemp())
        self.script = self.directory / "script.txt"
        self.script.write_text("mount /dev/nvme0n1p2 /mnt\nls /mnt/boot\n")

    def test_each_line_is_typed_once_and_the_stream_stays_open(self):
        import io
        stream = io.BytesIO()
        self.module.type_script(stream, self.script, 0, 0)
        self.assertEqual(stream.getvalue(), b"mount /dev/nvme0n1p2 /mnt\nls /mnt/boot\n")
        self.assertFalse(stream.closed, "communicate() flushes this stream after the thread returns")

    def test_a_closed_stream_is_not_an_error(self):
        import io
        stream = io.BytesIO()
        stream.close()
        self.module.type_script(stream, self.script, 0, 0)
