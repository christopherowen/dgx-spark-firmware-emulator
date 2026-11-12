"""The fan path, end to end in the emulator: DSDT arithmetic → SMC → the modelled service.

`tests/fixtures/fan_probe.c` runs as verified firmware and does exactly what the DSDT does: it
converts a level to an rpm with `WFCF`'s own formula, writes the four bytes through SMCCC
0xC400008D, and reads the result back with `_FST`'s request. The model in `monitor/fan.c` answers.

Two corrections were needed to get here, both worth keeping in the record: the SMC must be issued
with explicit register loads (a compiler-spilled x0 is a different function id), and the service
clears the register file to x17, so x16/x17 — the linker's scratch registers — must be declared as
clobbered or the application crashes on its next branch.
"""

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tests" / "fixtures" / "fan_probe.c"
PREPARED = ROOT / "firmware" / "prepared" / "2.158.15" / "uefi.fd"
STATE = ROOT / "firmware" / "state" / "board-2158.bin"

FMIN, FMAX = 975, 4350


def tool(name):
    from shutil import which
    found = which(name) or (ROOT / "build" / "host-tools" / "bin" / name)
    if isinstance(found, Path) and found.is_file():
        return str(found)
    if isinstance(found, str) and found:
        return found
    raise unittest.SkipTest(f"{name} is not available")


def build_application(directory: Path) -> Path:
    obj, image = directory / "fan.obj", directory / "fan.efi"
    subprocess.run([tool("clang"), "--target=aarch64-pc-windows-msvc", "-O2", "-ffreestanding",
                    "-nostdlib", "-c", str(SOURCE), "-o", str(obj)], check=True)
    subprocess.run([tool("lld-link"), "/machine:arm64", "/subsystem:efi_application",
                    "/entry:_start", "/nodefaultlib", f"/out:{image}", str(obj)], check=True)
    return image


@unittest.skipUnless(os.environ.get("SPARK_QEMU"), "set SPARK_QEMU to run the profile")
@unittest.skipUnless(PREPARED.is_file() and STATE.is_file(),
                     "the release and a device image are needed to boot")
class FanEndToEndTests(unittest.TestCase):
    def test_a_level_through_the_smc_comes_back_as_the_firmware_computes_it(self):
        with tempfile.TemporaryDirectory() as directory:
            image = build_application(Path(directory))
            result = subprocess.run(
                [sys.executable, "tools/boot_spark.py", "--pxe-test", "--board",
                 "--flash-image", str(STATE), "--firmware-version", "2.158.15",
                 "--pxe-image", str(image), "--pxe-marker", "FAN-OK", "--timeout", "120"],
                cwd=ROOT, capture_output=True, text=True,
                env={**os.environ, "PATH": f"{ROOT / 'build' / 'host-tools' / 'bin'}:{os.environ['PATH']}"})
            self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[-400:])
            log = Path((result.stdout + result.stderr).split("Local logs:")[-1].strip()) / "serial.log"
            text = log.read_text(errors="replace")

        self.assertIn("FAN-OK", text)
        pairs = {int(level): int(rpm) for level, rpm
                 in re.findall(r"^F (\d+) (\d+)$", text, re.M)}
        self.assertEqual(set(pairs), {0, 50, 100}, pairs)
        for level, rpm in pairs.items():
            expected = FMIN + ((FMAX - FMIN) * level) // 100
            self.assertEqual(rpm, expected, f"level {level}")
        for fan, pattern in ((0, r"^R0 (\d+)$"), (1, r"^R1 (\d+)$")):
            readback = re.search(pattern, text, re.M)
            self.assertIsNotNone(readback, f"fan {fan} answered its own status command")
            self.assertEqual(int(readback.group(1)), FMAX,
                             f"fan {fan}: the last level set was 100")


if __name__ == "__main__":
    unittest.main()
