"""Owner-specific publication markers: read from an ignored local file, never from the source.

Each case builds a throwaway repository with its own `firmware/publication-markers.txt` and asserts the
checker's verdict, which is the only way to test a rule about what may be *tracked*. The markers used
here are synthetic.
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

    def track(self, name: str, content) -> None:
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content)
        subprocess.run(["git", "-C", str(self.repo), "add", "-f", name], check=True)

    def local_markers(self, text: str) -> None:
        """The marker file is local: it is written, never added."""
        path = self.repo / "firmware" / "publication-markers.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def test_without_a_marker_file_only_firmware_is_checked(self):
        self.track("docs/notes.md", "measured on lab-host-7 yesterday\n")
        self.track("docs/looks-innocent.bin", "MZ not really a PE")
        self.assertEqual(check_publication.violations(self.repo), ["docs/looks-innocent.bin"])

    def test_a_marker_in_text_is_rejected_in_any_text_file(self):
        self.local_markers("# private hosts\nlab-host-7\n\n")
        self.track("docs/notes.md", "measured on Lab-Host-7 yesterday\n")
        self.track("research/record.json", '{"host": "lab-host-7"}\n')
        self.track("docs/diagram.svg", "<svg><text>lab-host-7</text></svg>\n")
        self.track("docs/other.md", "measured on a unit\n")
        self.assertEqual(check_publication.violations(self.repo),
                         ["docs/diagram.svg", "docs/notes.md", "research/record.json"])

    def test_a_marker_in_a_path_is_rejected(self):
        self.local_markers("secret_dump  # local-only work\n")
        self.track("tests/fixtures/secret_dump.c", "int main(void) { return 0; }\n")
        self.assertEqual(check_publication.violations(self.repo), ["tests/fixtures/secret_dump.c"])

    def test_binary_content_is_not_searched_as_text(self):
        self.local_markers("lab-host-7\n")
        self.track("docs/picture.png", b"\x89PNG\r\n\x1a\n\x00\x00lab-host-7")
        self.assertEqual(check_publication.violations(self.repo), [])

    def test_the_marker_file_itself_is_never_tracked(self):
        self.local_markers("lab-host-7\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "-f",
                        "firmware/publication-markers.txt"], check=True)
        self.assertEqual(check_publication.violations(self.repo), ["firmware/publication-markers.txt"])

    def test_ordinary_work_passes(self):
        self.local_markers("lab-host-7\n")
        self.track("docs/example.md", "# a page about the emulator\n")
        self.track("tests/test_example.py", "import unittest\n")
        self.assertEqual(check_publication.violations(self.repo), [])

    def test_this_repository_passes(self):
        self.assertEqual(check_publication.violations(ROOT), [],
                         "the tree must satisfy the rule it enforces, with whatever markers are local")


if __name__ == "__main__":
    unittest.main()
