"""The one owner rule that bit us: flash-readback work does not belong in this repository.

It was broken once — a readback payload and its tests reached this tree and had to be purged from history —
so the rule is a check now. Each case builds a throwaway repository and asserts the checker's verdict, which
is the only way to test a rule about what may be *tracked*.
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import check_publication  # noqa: E402


class PublicationRuleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="pubcheck-")
        self.repo = Path(self.directory.name) / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)

    def tearDown(self):
        self.directory.cleanup()

    def track(self, name: str, content: str) -> None:
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        subprocess.run(["git", "-C", str(self.repo), "add", "-f", name], check=True)

    def test_a_readback_payload_is_rejected_by_name(self):
        self.track("tests/fixtures/nor_dump.c", "int main(void) { return 0; }\n")
        self.assertEqual(check_publication.violations(self.repo), ["tests/fixtures/nor_dump.c"])

    def test_the_prearm_marker_is_rejected_by_content(self):
        self.track("monitor/monitor.c", "#ifdef SPARK_NOR_PREARM_DUMP\n#endif\n")
        self.assertEqual(check_publication.violations(self.repo), ["monitor/monitor.c"])

    def test_ordinary_work_passes(self):
        self.track("docs/example.md", "# a page about the emulator\n")
        self.track("tests/test_example.py", "import unittest\n")
        self.assertEqual(check_publication.violations(self.repo), [])

    def test_a_firmware_blob_is_still_rejected(self):
        self.track("docs/looks-innocent.bin", "MZ not really a PE")
        self.assertEqual(check_publication.violations(self.repo), ["docs/looks-innocent.bin"])

    def test_a_host_name_is_rejected(self):
        """Host names and lab paths belong to the private side, not to a public tree."""
        self.track("docs/notes.md", "measured on spark-0" + "1 yesterday\n")
        self.assertEqual(check_publication.violations(self.repo), ["docs/notes.md"])

    def test_a_lab_path_is_rejected(self):
        self.track("kernel/README.md", "ssh -F work/ssh/sparks_" + "config unit\n")
        self.assertEqual(check_publication.violations(self.repo), ["kernel/README.md"])

    def test_this_repository_passes(self):
        self.assertEqual(check_publication.violations(ROOT), [],
                         "the tree must satisfy the rule it enforces")


if __name__ == "__main__":
    unittest.main()
