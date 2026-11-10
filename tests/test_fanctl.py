"""The fan tool: the firmware's ACPI fan device, whatever a later firmware calls its limits.

`tools/fanctl.py` drives the board's own `_FSL` interface through the thermal class, so it works on
a firmware whose fan limits, state count or EC revision differ from the ones measured here. These
tests run it against synthetic thermal trees: the two shapes the latest firmware could present, a
machine that publishes no fan at all, and the boundary behaviour of the level mapping.
"""

import importlib.util
import io
import contextlib
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_tool():
    spec = importlib.util.spec_from_file_location("fanctl", ROOT / "tools" / "fanctl.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_device(root: Path, index: int, kind: str, maximum: int, current: int = 0) -> Path:
    device = root / f"cooling_device{index}"
    device.mkdir(parents=True)
    (device / "type").write_text(kind + "\n")
    (device / "max_state").write_text(f"{maximum}\n")
    (device / "cur_state").write_text(f"{current}\n")
    return device


class FanToolTests(unittest.TestCase):
    def setUp(self):
        self.fanctl = load_tool()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_a_fan_device_is_found_and_other_cooling_devices_are_not(self):
        make_device(self.root, 0, "Fan", 100)
        make_device(self.root, 1, "Processor", 7)
        make_device(self.root, 2, "intel_powerclamp", 4)
        devices = self.fanctl.fan_devices(self.root)
        self.assertEqual([device["type"] for device in devices], ["Fan"])

    def test_a_level_maps_onto_whatever_max_state_the_firmware_reports(self):
        # The latest firmware could publish 100 states or 12; the tool reads the device.
        for maximum in (100, 12, 5):
            with self.subTest(maximum=maximum):
                self.assertEqual(self.fanctl.level_to_state(0, maximum), 0)
                self.assertEqual(self.fanctl.level_to_state(100, maximum), maximum)
                state = self.fanctl.level_to_state(50, maximum)
                self.assertGreaterEqual(state, 1)
                self.assertLessEqual(state, maximum)

    def test_fine_grained_control_makes_the_state_equal_to_the_level(self):
        """This firmware's _FIF reports fine-grained control with a step of 1, so state 100 is the
        maximum and level 60 is state 60 — not a percentage of some other scale."""
        for level in (0, 1, 37, 60, 99, 100):
            self.assertEqual(self.fanctl.level_to_state(level, 100), level)

    def test_setting_a_level_writes_the_device_and_level_zero_is_the_slowest(self):
        device = make_device(self.root, 0, "Fan", 12)
        self.assertEqual(self.fanctl.set_level(self.fanctl.fan_devices(self.root), 50), 0)
        written = int((device / "cur_state").read_text())
        self.assertEqual(written, self.fanctl.level_to_state(50, 12))
        self.assertEqual(self.fanctl.set_level(self.fanctl.fan_devices(self.root), 0), 0)
        self.assertEqual(int((device / "cur_state").read_text()), 0)

    def test_a_level_the_firmware_did_not_take_is_reported_as_refused(self):
        """The vendor kernel cannot drive _FSL: the DSDT's FFixedHW region has no FFH descriptor, so
        the write is refused and cur_state stays where it was. The tool must say so, not report a
        level it did not set."""
        device = make_device(self.root, 0, "Fan", 100, current=0)
        (device / "cur_state").write_text("0\n")
        # a device whose cur_state never changes: what the AFH-less firmware looks like
        original = (device / "cur_state")
        original.chmod(0o444)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = self.fanctl.set_level(self.fanctl.fan_devices(self.root), 50)
        original.chmod(0o644)
        self.assertEqual(code, 1)
        self.assertIn("REFUSED", buffer.getvalue())

    def test_a_dry_run_reports_without_writing(self):
        device = make_device(self.root, 0, "Fan", 100, current=7)
        self.assertEqual(self.fanctl.set_level(self.fanctl.fan_devices(self.root), 80,
                                                dry_run=True), 0)
        self.assertEqual(int((device / "cur_state").read_text()), 7)

    def test_no_fan_device_is_reported_rather_than_invented(self):
        make_device(self.root, 0, "Processor", 7)
        devices = self.fanctl.fan_devices(self.root)
        self.assertEqual(devices, [])
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            self.assertEqual(self.fanctl.status(devices), 1)
        self.assertIn("does not publish", buffer.getvalue())
        self.assertIn("spark_fan", buffer.getvalue(),
                      "the message must point at the route that does not need the ACPI device")
        # Nothing was changed because nothing was found.
        self.assertEqual(int((self.root / "cooling_device0" / "cur_state").read_text()), 0)

    def test_the_cli_refuses_a_level_outside_the_dsl_range(self):
        make_device(self.root, 0, "Fan", 100)
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                import sys
                argv = sys.argv
                sys.argv = ["fanctl.py", "--root", str(self.root), "set", "150"]
                try:
                    self.fanctl.main()
                finally:
                    sys.argv = argv


if __name__ == "__main__":
    unittest.main()
