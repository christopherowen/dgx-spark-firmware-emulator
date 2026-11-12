"""The memory a boot may use: the host's free memory, or the cgroup's headroom if smaller.

Reading `/proc/meminfo` alone is what made a 3 GiB guest die with `exit 137` while `free` reported
7.6 GiB available: the kernel applied this container's cgroup limit, not the host's memory, and killed
the process instead of letting the harness refuse. The harness now takes the smaller of the two, so the
failure is a message rather than a kill.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tools.boot_spark as boot_spark  # noqa: E402
from tools.boot_spark import available_megabytes  # noqa: E402

GIB = 1024 * 1024 * 1024


def total_megabytes():
    """`MemTotal` in MiB, the only bound a live `MemAvailable` reading can be checked against."""
    with open("/proc/meminfo", encoding="ascii") as handle:
        for line in handle:
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) // 1024
    raise AssertionError("/proc/meminfo has no MemTotal line")


class AdmissionTests(unittest.TestCase):
    def root(self, files):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        for name, value in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value)
        return root

    def test_a_cgroup_limit_smaller_than_the_host_wins(self):
        root = self.root({"memory.max": str(4 * GIB), "memory.current": str(1 * GIB)})
        self.assertEqual(available_megabytes(root, host_megabytes=8192), 3 * 1024)
        # The host is the smaller one here, and the answer follows it rather than the cgroup.
        self.assertEqual(available_megabytes(root, host_megabytes=1024), 1024)

    def test_an_unlimited_cgroup_falls_back_to_the_host(self):
        root = self.root({"memory.max": "max", "memory.current": "0"})
        self.assertEqual(available_megabytes(root, host_megabytes=8192), 8192)

    def test_the_v1_hierarchy_is_read_too(self):
        root = self.root({"memory/memory.limit_in_bytes": str(2 * GIB),
                          "memory/memory.usage_in_bytes": str(512 * 1024 * 1024)})
        self.assertEqual(available_megabytes(root, host_megabytes=8192), 1536)

    def test_a_v1_sentinel_for_unlimited_is_not_a_limit(self):
        sentinel = str((1 << 63) - 4096)
        root = self.root({"memory/memory.limit_in_bytes": sentinel,
                          "memory/memory.usage_in_bytes": "0"})
        self.assertEqual(available_megabytes(root, host_megabytes=8192), 8192)

    def test_usage_above_the_limit_reports_nothing_rather_than_a_negative(self):
        root = self.root({"memory.max": str(1 * GIB), "memory.current": str(2 * GIB)})
        self.assertEqual(available_megabytes(root, host_megabytes=8192), 0)

    def test_a_host_without_a_reading_claims_nothing(self):
        """No `/proc/meminfo` (macOS) and no cgroup: the admission step gets None and does not refuse."""
        with mock.patch.object(boot_spark, "open", create=True, side_effect=OSError):
            self.assertIsNone(available_megabytes(self.root({})))

    @unittest.skipUnless(Path("/proc/meminfo").is_file(), "the host has no /proc/meminfo")
    def test_without_an_injected_host_value_proc_meminfo_is_read(self):
        """One read, checked for plausibility: the reading itself is not something to pin."""
        available = available_megabytes(self.root({}))
        self.assertIsNotNone(available)
        self.assertGreater(available, 0)
        self.assertLessEqual(available, total_megabytes())


if __name__ == "__main__":
    unittest.main()
