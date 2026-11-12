"""Two namespaces at once: media on one, a target disk on the other.

Installing the recovery system needs both at the same time — the installer reads from the volume that
carries the kernel and initrd and writes to a disk it partitions. Before this, `--nvme-image` took a
single path, so the target had nowhere to appear and the installer's disk step could not be reached.

The harness attaches each image as its own PCI function on bus 1 (slot 2, then 3, …) and snapshots
each, so a run can never write the caller's files. What is checked here is the command line the
harness builds and what it records about each image; that two controllers answer on the bus is the
probe's job, in `tests/fixtures/nvme_probe.c`, and needs the profile.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tools.boot_spark as boot_spark  # noqa: E402


def attach(images) -> tuple[str, dict]:
    """The QEMU arguments and receipt entries for these images, without running a boot.

    `path_for=str` bypasses the harness's own rule that a real run's images live under the ignored
    `firmware/` tree: that rule has its own test, and applying it here would only test the temporary
    directory layout this file happens to use.
    """
    arguments, entries = boot_spark.nvme_arguments(images, path_for=str)
    return " ".join(arguments), {"nvme_images": entries}


def attach_in_child(image: Path) -> tuple[dict, float]:
    """`attach([image])` in a fresh interpreter: its receipt, and that process's own peak RSS in MiB.

    A child's peak is exact for the work it did, where the test process's own figure would include
    whatever earlier tests left behind. `os.wait4` reports it on Linux (KiB) and macOS (bytes) alike.
    """
    code = ("import json, sys; sys.path.insert(0, sys.argv[1]); import tools.boot_spark as b; "
            "print(json.dumps(b.nvme_arguments([sys.argv[2]], path_for=str)[1]))")
    process = subprocess.Popen([sys.executable, "-c", code, str(ROOT), str(image)],
                               stdout=subprocess.PIPE)
    output = process.stdout.read()
    process.stdout.close()
    _, status, usage = os.wait4(process.pid, 0)
    process.returncode = os.waitstatus_to_exitcode(status)
    if process.returncode != 0:
        raise AssertionError(f"the hashing child exited with {process.returncode}")
    unit = 1 if sys.platform == "darwin" else 1024
    return {"nvme_images": json.loads(output)}, usage.ru_maxrss * unit / (1024 * 1024)


class TwoNamespaceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.media = Path(self.directory.name) / "media.img"
        self.target = Path(self.directory.name) / "target.img"
        self.media.write_bytes(b"M" * 4096)
        self.target.write_bytes(b"T" * 8192)

    def test_one_image_still_behaves_as_before(self):
        command, record = attach([self.media])
        self.assertIn("id=nvme0", command)
        self.assertIn("addr=2", command)
        self.assertNotIn("id=nvme1", command)
        self.assertEqual(len(record["nvme_images"]), 1)
        self.assertEqual(record["nvme_images"][0]["path"], str(self.media))

    def test_a_second_image_gets_its_own_slot_and_snapshot(self):
        command, record = attach([self.media, self.target])
        self.assertIn("id=nvme0", command)
        self.assertIn("id=nvme1", command)
        self.assertIn("addr=2", command)
        self.assertIn("addr=3", command)
        self.assertEqual(command.count("snapshot=on"), 2, "every namespace is snapshotted")
        self.assertEqual([entry["path"] for entry in record["nvme_images"]],
                         [str(self.media), str(self.target)])
        self.assertEqual([entry["size"] for entry in record["nvme_images"]], [4096, 8192])

    def test_each_receipt_names_the_image_it_snapshotted(self):
        _, record = attach([self.media])
        entry = record["nvme_images"][0]
        self.assertEqual(entry["pci"], "synthetic: segment 0 bus 1 slot 2")
        self.assertRegex(entry["sha256"], r"^[0-9a-f]{64}$")

    def test_a_large_image_is_hashed_without_being_loaded(self):
        """A target disk is gigabytes; reading it whole is how a boot becomes an OOM kill.

        A sparse 2 GiB file costs nothing on disk and almost nothing to hash, so the check is that the
        receipt's digest is the streamed one and that the process did not balloon.
        """
        big = Path(self.directory.name) / "big.img"
        with big.open("wb") as handle:
            handle.truncate(2 * 1024 * 1024 * 1024)
        record, peak_mb = attach_in_child(big)
        self.assertLess(peak_mb, 256, f"hashing peaked at {peak_mb:.0f} MiB")
        self.assertEqual(record["nvme_images"][0]["size"], 2 * 1024 * 1024 * 1024)
        import hashlib
        expected = hashlib.sha256()
        with big.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                expected.update(block)
        self.assertEqual(record["nvme_images"][0]["sha256"], expected.hexdigest())

    def test_more_than_four_is_refused(self):
        extra = [self.media] * 5
        with self.assertRaises(ValueError):
            attach(extra)


if __name__ == "__main__":
    unittest.main()
