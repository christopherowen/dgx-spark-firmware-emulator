"""The CPU clock tool, driven against a tree shaped like the kernel's cpufreq class.

The firmware publishes CPPC on every processor device, so the OS owns this lever; what this test can
prove without a machine is the tool's behaviour against that interface — reading it, capping it,
refusing a number the firmware does not offer, and putting the cap back.
"""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from tools import cpu_clocks


def make_policy(root, name, driver="cppc_cpufreq", low=852000, high=3916000, current=3916000,
                governor="schedutil", epp="balance_performance"):
    path = Path(root) / name
    path.mkdir(parents=True, exist_ok=True)
    (path / "scaling_driver").write_text(driver + "\n")
    (path / "scaling_governor").write_text(governor + "\n")
    (path / "cpuinfo_min_freq").write_text(f"{low}\n")
    (path / "cpuinfo_max_freq").write_text(f"{high}\n")
    (path / "scaling_min_freq").write_text(f"{low}\n")
    (path / "scaling_max_freq").write_text(f"{current}\n")
    (path / "scaling_cur_freq").write_text(f"{current}\n")
    if epp:
        (path / "energy_performance_preference").write_text(epp + "\n")
        (path / "energy_performance_available_preferences").write_text(
            "default performance balance_performance balance_power power\n")
    return path


class CpuClockTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="cpufreq-")
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def test_policies_are_read_with_their_ranges(self):
        make_policy(self.root, "policy0")
        make_policy(self.root, "policy4", current=2600000)
        found = cpu_clocks.policies(self.root)
        self.assertEqual([policy["name"] for policy in found], ["policy0", "policy4"])
        self.assertEqual(found[1]["scaling_max"], 2600000)
        self.assertEqual(found[0]["driver"], "cppc_cpufreq")

    def test_a_cap_writes_every_policy_and_saves_what_was_there(self):
        make_policy(self.root, "policy0")
        make_policy(self.root, "policy4")
        self.assertEqual(cpu_clocks.set_cap(cpu_clocks.policies(self.root), 3000000, self.root), 0)
        for name in ("policy0", "policy4"):
            self.assertEqual((self.root / name / "scaling_max_freq").read_text().strip(), "3000000")
        saved = json.loads((self.root / ".cpu_clocks_backup.json").read_text())
        self.assertEqual(saved["policy0"]["scaling_max"], 3916000)

    def test_restoring_puts_the_saved_cap_back(self):
        make_policy(self.root, "policy0")
        cpu_clocks.set_cap(cpu_clocks.policies(self.root), 2000000, self.root)
        self.assertEqual(cpu_clocks.restore(cpu_clocks.policies(self.root), self.root), 0)
        self.assertEqual((self.root / "policy0" / "scaling_max_freq").read_text().strip(), "3916000")

    def test_a_cap_above_the_firmware_ceiling_is_refused_not_clamped(self):
        make_policy(self.root, "policy0")
        buffer = io.StringIO()
        with contextlib.redirect_stderr(buffer), contextlib.redirect_stdout(io.StringIO()):
            code = cpu_clocks.main(["--root", str(self.root), "cap", "9000000"])
        self.assertEqual(code, 1)
        self.assertIn("above this CPU's cpuinfo_max_freq", buffer.getvalue())
        self.assertEqual((self.root / "policy0" / "scaling_max_freq").read_text().strip(), "3916000")

    def test_cap_max_removes_the_cap(self):
        make_policy(self.root, "policy0", current=2000000)
        self.assertEqual(cpu_clocks.set_cap(cpu_clocks.policies(self.root), "max", self.root), 0)
        self.assertEqual((self.root / "policy0" / "scaling_max_freq").read_text().strip(), "3916000")

    def test_an_energy_preference_outside_the_published_list_is_not_written(self):
        make_policy(self.root, "policy0")
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cpu_clocks.set_epp(cpu_clocks.policies(self.root), "turbo", self.root)
        self.assertEqual(code, 1)
        self.assertIn("not one of", buffer.getvalue())
        self.assertEqual(
            (self.root / "policy0" / "energy_performance_preference").read_text().strip(),
            "balance_performance")

    def test_a_machine_without_cpufreq_is_reported_rather_than_invented(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            self.assertEqual(cpu_clocks.status([]), 1)
        self.assertIn("no cpufreq policy found", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
