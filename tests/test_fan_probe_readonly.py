"""The build we would take to a unit where the fans are not in the ACPI table.

`ACUM027` declares no `FAN0`, but its firmware enumerates the fan partition (`0x8003`,
`884a63a0-…`), so the question for that firmware is whether the service answers the two documented
status reads. The `READ_ONLY` build of `tests/fixtures/fan_probe.c` can ask exactly those two questions
and nothing else — it cannot pin a fan even if asked. This runs it end to end under the profile.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tests" / "fixtures" / "fan_probe.c"
STATE = ROOT / "firmware" / "state" / "board-2158.bin"


def tool(name: str) -> str:
    found = shutil.which(name) or ROOT / "build" / "host-tools" / "bin" / name
    if isinstance(found, Path) and found.is_file():
        return str(found)
    if isinstance(found, str) and found:
        return found
    raise unittest.SkipTest(f"{name} is not available")


def build_read_only(directory: Path) -> Path:
    obj, image = directory / "fan_ro.obj", directory / "fan_ro.efi"
    subprocess.run([tool("clang"), "--target=aarch64-pc-windows-msvc", "-O2", "-ffreestanding",
                    "-nostdlib", "-DREAD_ONLY", "-c", str(SOURCE), "-o", str(obj)], check=True)
    subprocess.run([tool("lld-link"), "/machine:arm64", "/subsystem:efi_application",
                    "/entry:_start", "/nodefaultlib", f"/out:{image}", str(obj)], check=True)
    return image


@unittest.skipUnless(os.environ.get("SPARK_QEMU"), "set SPARK_QEMU to run the profile")
@unittest.skipUnless(STATE.is_file(), "a device image is needed to boot")
class FanProbeReadOnlyTests(unittest.TestCase):
    def test_the_read_only_build_asks_only_the_two_status_questions(self):
        with tempfile.TemporaryDirectory() as directory:
            image = build_read_only(Path(directory))
            result = subprocess.run(
                [sys.executable, "tools/boot_spark.py", "--pxe-test", "--board",
                 "--flash-image", str(STATE), "--firmware-version", "2.158.15",
                 "--pxe-image", str(image), "--pxe-marker", "READ-ONLY-OK", "--timeout", "200"],
                cwd=ROOT, capture_output=True, text=True,
                env={**os.environ, "PATH": f"/usr/lib/llvm-18/bin:{ROOT / 'build' / 'host-tools' / 'bin'}:{os.environ['PATH']}"})
            self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[-400:])
            log = Path((result.stdout + result.stderr).split("Local logs:")[-1].strip()) / "serial.log"
            text = log.read_text(errors="replace")

        self.assertIn("READ-ONLY-OK", text)
        answers = dict(re.findall(r"^R([01]) (\d+)$", text, re.M))
        self.assertEqual(set(answers), {"0", "1"}, "both fans answered their own status command")
        self.assertNotRegex(text, r"^F \d+ \d+$",
                            "the read-only build never runs the level-setting sequence")


if __name__ == "__main__":
    unittest.main()
