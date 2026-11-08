"""Structural validation of a vendor 32 MiB NVAR readback."""

import tempfile
import unittest
from pathlib import Path

import subprocess
import sys

from tools.nor_image import ImageError, inspect, lift, stage

SIZE = 0x2000000
BANK = 0x40000


def entry(name: str, value: bytes, attributes: int = 0x83, index: int = 0) -> bytes:
    """Real store shape: NVAR, u16 size, 3 state bytes, attributes, index, name, data.

    Entries are packed back to back with no alignment padding, which is why the
    walker advances by the declared size exactly.
    """
    body = bytes([index]) + name.encode("ascii") + b"\x00" + value
    size = 10 + len(body)
    return b"NVAR" + size.to_bytes(2, "little") + b"\xff\xff\xff" + bytes([attributes]) + body


def image(banks: tuple[bytes, bytes] = (b"", b"")) -> bytes:
    data = bytearray(b"\xff" * SIZE)
    for index, payload in enumerate(banks):
        base = index * BANK
        data[base + 0x28:base + 0x2c] = b"_FVH"
        if payload:
            data[base + 0x60:base + 0x60 + len(payload)] = payload
    return bytes(data)


class NorImageTests(unittest.TestCase):
    def write(self, data: bytes) -> Path:
        handle = tempfile.NamedTemporaryFile(delete=False, suffix=".bin")
        handle.write(data)
        handle.close()
        self.addCleanup(Path(handle.name).unlink, missing_ok=True)
        return Path(handle.name)

    def test_real_shape_is_accepted_with_both_banks(self):
        payload = entry("Setup", b"\x01\x02") + entry("PlatformLang", b"en-US\x00")
        report = inspect(self.write(image((payload, b""))))
        self.assertEqual(report.size, SIZE)
        self.assertEqual([bank.entries for bank in report.banks], [2, 0])
        self.assertEqual(report.banks[0].last_entry_end, 0x60 + len(payload))
        self.assertEqual([bank.header_offset for bank in report.banks], [0x28, 0x40028])

    def bank_zero_capture(self) -> Path:
        """Exactly one bank, as an older 1 MiB-era model would have produced."""
        payload = entry("Setup", b"\x01\x02") + entry("DmiArray", b"\x01\x00\x00\x00\x00")
        whole = image((payload, b""))
        return self.write(whole[:BANK])

    def test_lift_places_one_bank_into_a_whole_part(self):
        source = self.bank_zero_capture()
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "lifted.bin"
            lift(source, destination)
            self.assertEqual(destination.stat().st_size, SIZE)
            report = inspect(destination)
            self.assertEqual(report.size, SIZE)
            self.assertEqual(report.banks[0].entries, 2)
            self.assertEqual(report.banks[1].entries, 0)

    def test_lift_refuses_any_length_that_is_not_one_bank(self):
        for size in (BANK - 1, BANK + 1, SIZE):
            with tempfile.TemporaryDirectory() as directory:
                source = Path(directory) / "capture.bin"
                source.write_bytes(b"\xff" * size)
                with self.assertRaises(ImageError):
                    lift(source, Path(directory) / "out.bin")
                self.assertFalse((Path(directory) / "out.bin").exists())

    def test_lift_cli_produces_the_part_and_never_overwrites(self):
        source = self.bank_zero_capture()
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "cli.bin"
            first = subprocess.run([sys.executable, "tools/nor_image.py", "lift",
                                    str(source), str(destination)],
                                   capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            self.assertEqual(destination.stat().st_size, SIZE)
            second = subprocess.run([sys.executable, "tools/nor_image.py", "lift",
                                     str(source), str(destination)],
                                    capture_output=True, text=True)
            self.assertNotEqual(second.returncode, 0)
            self.assertIn("refusing to overwrite", second.stdout)

    def test_both_banks_populated(self):
        one = entry("Setup", b"\x00")
        report = inspect(self.write(image((one, one + one))))
        self.assertEqual([bank.entries for bank in report.banks], [1, 2])

    def test_erased_image_is_rejected(self):
        with self.assertRaisesRegex(ImageError, "no firmware-volume signature"):
            inspect(self.write(b"\xff" * SIZE))

    def test_wrong_size_is_rejected(self):
        # neither the 32 MiB model nor the 64 MiB part, so no run size can match it
        for size in (0x100000, 0x2000001, 0x8000000):
            with self.assertRaises(ImageError):
                inspect(self.write(b"\xff" * size))

    def test_both_modelled_sizes_are_accepted(self):
        for size in (0x2000000, 0x4000000):
            report = inspect(self.write(image((entry("Setup", b"\x00"),)) + b"\xff" * (size - 0x2000000)))
            self.assertEqual(report.size, size)

    def test_header_without_entries_is_rejected(self):
        with self.assertRaisesRegex(ImageError, "no NVAR entries"):
            inspect(self.write(image((b"", b""))))

    def test_entry_running_past_its_bank_is_rejected(self):
        # exercise the walker directly with a bank small enough to overflow
        from tools.nor_image import _walk_bank

        data = bytearray(b"\xff" * 0x1000)
        data[0x28:0x2c] = b"_FVH"
        data[0x60:0x6a] = b"NVAR" + (0x200).to_bytes(2, "little") + b"\x00\x00\x00\x83"
        with self.assertRaisesRegex(ImageError, "runs past its bank"):
            _walk_bank(bytes(data), 0, 0, 0x100)

    def test_dead_attributes_are_rejected(self):
        with self.assertRaisesRegex(ImageError, "dead attributes"):
            inspect(self.write(image((entry("Setup", b"", attributes=0x00), b""))))

    def test_stage_never_overwrites_and_preserves_hash(self):
        source = self.write(image((entry("Setup", b"\x00"), b"")))
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        destination = Path(temporary.name) / "staged.bin"
        source_sha, staged_sha = stage(source, destination)
        self.assertEqual(source_sha, staged_sha)
        with self.assertRaisesRegex(ImageError, "refusing to overwrite"):
            stage(source, destination)


if __name__ == "__main__":
    unittest.main()
