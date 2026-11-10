"""The power-limit tool: it edits the fields the firmware names, at a base the firmware states.

`tools/pl_limits.py` reads and writes the OS limit slots of the SPBM structure using the offsets
extracted from the vendor DSDT. The base comes from the firmware's own `_SB.MTEL` resource
descriptor (0x05170000), so an image is read at it by default; a live device node must still state
its address explicitly, because writing memory is a separate decision. A test over a synthetic
window proves the offsets, the write, the backup and the restore without pretending to know what the
real structure contains.
"""

import importlib.util
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_tool():
    spec = importlib.util.spec_from_file_location("pl_limits", ROOT / "tools" / "pl_limits.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PowerLimitTests(unittest.TestCase):
    def setUp(self):
        self.tool = load_tool()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.image = Path(self.directory.name) / "window.bin"
        # A window with the documented OS slots pre-set to recognisable values.
        window = bytearray(0x400)
        for name, value in (("SPBM_PL1_VAL_OS_OFFSET", 30000),
                            ("SPBM_PL2_VAL_OS_OFFSET", 60000),
                            ("SPBM_PL1_VAL_OFFSET", 30000)):
            struct.pack_into("<I", window, self.tool.offsets()[name], value)
        self.image.write_bytes(bytes(window))

    def invoke(self, *arguments):
        return subprocess.run([sys.executable, str(ROOT / "tools" / "pl_limits.py"),
                               "--image", str(self.image), *arguments],
                              capture_output=True, text=True)

    def test_the_offsets_come_from_the_extracted_map(self):
        table = self.tool.offsets()
        self.assertEqual(table["SPBM_PL1_VAL_OS_OFFSET"], 0x100)
        self.assertEqual(table["SPBM_PL2_VAL_OS_OFFSET"], 0x104)
        self.assertEqual(table["SPBM_PL1_VAL_OFFSET"], 0x160)

    def test_reading_reports_both_the_requested_and_the_effective_values(self):
        result = self.invoke("--read")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout, r"os PL1 =\s+30000")
        self.assertRegex(result.stdout, r"effective PL1 =\s+30000")

    def test_a_write_is_backed_up_and_lands_in_the_os_slot(self):
        result = self.invoke("--set", "pl1=45000")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("backup written", result.stdout)
        backup = Path(f"{self.image}.orig")
        self.assertTrue(backup.is_file())
        window = self.image.read_bytes()
        table = self.tool.offsets()
        self.assertEqual(struct.unpack_from("<I", window, table["SPBM_PL1_VAL_OS_OFFSET"])[0], 45000)
        # The effective value is not touched by writing the OS slot: the firmware decides that.
        self.assertEqual(struct.unpack_from("<I", window, table["SPBM_PL1_VAL_OFFSET"])[0], 30000)

    def test_a_restore_puts_the_window_back(self):
        self.assertEqual(self.invoke("--set", "pl1=45000").returncode, 0)
        result = self.invoke("--restore", f"{self.image}.orig")
        self.assertEqual(result.returncode, 0, result.stderr)
        table = self.tool.offsets()
        self.assertEqual(struct.unpack_from("<I", self.image.read_bytes(),
                                            table["SPBM_PL1_VAL_OS_OFFSET"])[0], 30000)

    def test_an_unknown_limit_is_refused(self):
        result = self.invoke("--set", "pl9=1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown limit", result.stderr)

    def test_a_device_node_without_a_base_is_refused(self):
        result = subprocess.run([sys.executable, str(ROOT / "tools" / "pl_limits.py"),
                                 "--dev-mem", "/dev/null", "--read"],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--base is required", result.stderr)


if __name__ == "__main__":
    unittest.main()
