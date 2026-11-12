"""The guest-side probe must build into something an aarch64 Linux can execute as /init.

It is injected as `/init` into an initramfs, so a wrong target, a dynamic link or a missing entry
point fails as "No working init found" — a panic that names none of the causes. This checks the
artefact rather than the source.
"""

import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tests" / "fixtures" / "guest_probe.c"


def tool(name):
    found = shutil.which(name)
    if found:
        return found
    bundled = ROOT / "build" / "host-tools" / "bin" / name
    if bundled.is_file():
        return str(bundled)
    return None


@unittest.skipUnless(tool("clang") and tool("ld.lld"), "building the probe needs clang and lld")
class GuestProbeTests(unittest.TestCase):
    def build(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        image = Path(directory.name) / "probe"
        subprocess.run([tool("clang"), "--target=aarch64-linux-gnu", "-nostdlib", "-static", "-O2",
                        "-ffreestanding", "-fno-builtin", "-fno-stack-protector",
                        "-fuse-ld=lld", "-Wl,-e,_start", str(SOURCE), "-o", str(image)],
                       check=True, capture_output=True)
        return image.read_bytes()

    def test_it_is_an_aarch64_static_executable(self):
        data = self.build()
        self.assertEqual(data[:4], b"\x7fELF")
        machine = struct.unpack_from("<H", data, 18)[0]
        self.assertEqual(machine, 183, "aarch64")
        # A PT_INTERP segment would mean a dynamic loader is needed, and the guest has none on the
        # path a probe can rely on.
        program_offset = struct.unpack_from("<Q", data, 32)[0]
        count = struct.unpack_from("<H", data, 56)[0]
        kinds = [struct.unpack_from("<I", data, program_offset + i * 56)[0] for i in range(count)]
        self.assertNotIn(3, kinds, "statically linked, so no interpreter segment")

    def test_it_carries_its_own_output_markers(self):
        """The markers are how a run's log proves the probe ran rather than the vendor init."""
        self.assertIn(b"GUEST-PROBE-BEGIN", self.build())
        self.assertIn(b"GUEST-PROBE-END", self.build())


if __name__ == "__main__":
    unittest.main()
