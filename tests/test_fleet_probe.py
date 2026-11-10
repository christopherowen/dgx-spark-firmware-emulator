"""The read-only fleet probe: what it says about three kinds of unit, from synthetic sysfs trees.

The probe answers two questions from sysfs alone. Its first version got one of them wrong — it counted
every cooling device as an "ACPI fan surface", including the six PCIe link-speed limiters on a unit that
has no fan device at all. These tests pin the classification with trees that have exactly the shapes we
met: a fan, a PCIe limiter, a module-provided cooling device, and a firmware with no fan surface.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import fleet_probe  # noqa: E402


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n")


class FleetProbeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="fleet-probe-")
        base = Path(self.directory.name)
        self.ffa, self.thermal, self.hwmon = base / "ffa", base / "thermal", base / "hwmon"

    def tearDown(self):
        self.directory.cleanup()

    def add_partition(self, index: int, partition_id: str, uuid: str, driver: str = "") -> None:
        entry = self.ffa / f"arm-ffa-{index}"
        write(entry / "partition_id", partition_id)
        write(entry / "uuid", uuid)
        if driver:
            write(entry / "driver-name", driver)

    def add_cooling(self, name: str, kind: str, maximum: str = "1") -> None:
        write(self.thermal / name / "type", kind)
        write(self.thermal / name / "max_state", maximum)
        write(self.thermal / name / "cur_state", "0")

    def add_hwmon(self, name: str, identifier: str, channels=()) -> None:
        entry = self.hwmon / name
        write(entry / "name", identifier)
        for channel in channels:
            write(entry / channel, "1000")

    def test_a_firmware_with_the_service_and_no_fan_surface(self):
        self.add_partition(17, "0x8003", fleet_probe.FAN_UUID)
        for index in range(6):
            self.add_cooling(f"cooling_device{index}", f"PCIe_Port_Link_Speed_000{index}:00:00.0", "4")
        data = fleet_probe.report(self.ffa, self.thermal, self.hwmon)
        self.assertTrue(data["fan_service"]["present"])
        self.assertFalse(data["acpi_fan_surface"]["present"],
                         "six PCIe limiters are not a fan surface")

    def test_a_firmware_with_a_fan_device(self):
        self.add_cooling("cooling_device0", "Fan")
        data = fleet_probe.report(self.ffa, self.thermal, self.hwmon)
        self.assertTrue(data["acpi_fan_surface"]["present"])
        self.assertFalse(data["fan_service"]["present"], "no FF-A entry in this tree")

    def test_a_module_registered_cooling_device_counts(self):
        self.add_cooling("cooling_device3", "spark_fan")
        data = fleet_probe.report(self.ffa, self.thermal, self.hwmon)
        self.assertTrue(data["acpi_fan_surface"]["present"])

    def test_a_hwmon_pwm_channel_counts(self):
        self.add_hwmon("hwmon0", "nct6775", ["pwm1", "fan1_input"])
        data = fleet_probe.report(self.ffa, self.thermal, self.hwmon)
        self.assertTrue(data["acpi_fan_surface"]["present"])
        self.assertEqual(data["acpi_fan_surface"]["hwmon_pwm_channels"], ["pwm1"])
        self.assertEqual(data["acpi_fan_surface"]["hwmon_fan_channels"], ["fan1_input"])

    def test_the_fan_service_must_match_the_uuid_exactly(self):
        self.add_partition(5, "0x8003", "884a63a0-3285-4120-83aa-eec008a0a54")   # one nibble short
        data = fleet_probe.report(self.ffa, self.thermal, self.hwmon)
        self.assertFalse(data["fan_service"]["present"])

    def test_the_module_state_is_reported(self):
        """The read that answers the fleet question comes from the module's cooling device."""
        self.add_partition(17, "0x8003", fleet_probe.FAN_UUID)
        self.add_cooling("cooling_device4", "spark_fan")
        self.add_cooling("cooling_device5", "PCIe_Port_Link_Speed_0000:00:00.0", "4")
        modules = Path(self.directory.name) / "modules"
        (modules / "spark_fan").mkdir(parents=True)
        data = fleet_probe.report(self.ffa, self.thermal, self.hwmon, modules)
        self.assertTrue(data["module"]["loaded"])
        self.assertEqual([device["type"] for device in data["module"]["devices"]], ["spark_fan"])

    def test_missing_trees_report_absence_rather_than_failing(self):
        data = fleet_probe.report(self.ffa, self.thermal, self.hwmon)
        self.assertFalse(data["fan_service"]["present"])
        self.assertFalse(data["acpi_fan_surface"]["present"])
        self.assertEqual(json.dumps(data).count("partition_id"), 0)


if __name__ == "__main__":
    unittest.main()
