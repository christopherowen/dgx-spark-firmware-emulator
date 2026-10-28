"""Exercise local storage and publication rules using synthetic downloads only."""

import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools.check_publication import violations
from tools.download_firmware import download


class Response(io.BytesIO):
    def geturl(self):
        return "https://fwupd.org/downloads/synthetic-fixture.cab"


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / ".gitignore").write_text("/firmware/**\n!/firmware/README.md\n")
        self.data = b"Synthetic fixture. Not vendor firmware.\n"
        self.digest = hashlib.sha256(self.data).hexdigest()
        self.url = "https://fwupd.org/downloads/synthetic-fixture.cab"

    def fetch(self, **kwargs):
        options = {"url": self.url, "sha256": self.digest, "filename": "spark-soc.cab",
                   "root": self.root}
        options.update(kwargs)
        return download(**options)

    @patch("tools.download_firmware.open_https")
    def test_verified_bytes_and_metadata_are_ignored_and_not_added(self, opener):
        opener.return_value = Response(self.data)
        result = self.fetch()
        self.assertEqual(result, self.root / "firmware/downloads/spark-soc.cab")
        self.assertEqual(result.read_bytes(), self.data)
        record = json.loads(result.with_name(result.name + ".source.json").read_text())
        self.assertEqual(record["sha256"], self.digest)
        self.assertEqual(record["url"], self.url)
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)
        tracked = subprocess.check_output(["git", "-C", str(self.root), "ls-files"], text=True)
        self.assertEqual(tracked.splitlines(), [".gitignore"])

    @patch("tools.download_firmware.open_https")
    def test_bad_checksum_discards_partial_artifact_and_metadata(self, opener):
        opener.return_value = Response(self.data)
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            self.fetch(sha256="0" * 64)
        self.assertEqual(list((self.root / "firmware/downloads").iterdir()), [])

    @patch("tools.download_firmware.open_https")
    def test_unsafe_paths_urls_and_missing_ignore_are_rejected_before_download(self, opener):
        for options in ({"filename": "../../escape.cab"}, {"url": "http://fwupd.org/file"},
                        {"sha256": "missing"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.fetch(**options)
        (self.root / ".gitignore").write_text("")
        with self.assertRaisesRegex(ValueError, "Git-ignored"):
            self.fetch()
        opener.assert_not_called()

    @patch("tools.download_firmware.open_https")
    def test_existing_image_is_preserved(self, opener):
        target = self.root / "firmware/downloads/spark-soc.cab"
        target.parent.mkdir(parents=True)
        target.write_bytes(self.data)
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.fetch()
        self.assertEqual(target.read_bytes(), self.data)
        opener.assert_not_called()

    @patch("tools.download_firmware.open_https")
    def test_symlink_directory_is_rejected(self, opener):
        external = self.root / "external"
        external.mkdir()
        (self.root / "firmware").symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            self.fetch()
        opener.assert_not_called()

    def test_force_added_or_renamed_firmware_fails_publication_check(self):
        target = self.root / "firmware/downloads/spark-soc.cab"
        target.parent.mkdir(parents=True)
        target.write_bytes(self.data)
        (self.root / "renamed-image.dat").write_bytes(b"MSCF" + self.data)
        (self.root / "renamed-mm.dat").write_bytes(b"SPKG" + self.data)
        subprocess.run(["git", "-C", str(self.root), "add", "-f", str(target)], check=True)
        subprocess.run(["git", "-C", str(self.root), "add", "renamed-image.dat"], check=True)
        subprocess.run(["git", "-C", str(self.root), "add", "renamed-mm.dat"], check=True)
        # Even deleting the local copy cannot hide a firmware blob already staged.
        (self.root / "renamed-image.dat").unlink()
        self.assertEqual(violations(self.root), ["firmware/downloads/spark-soc.cab", "renamed-image.dat",
                                                "renamed-mm.dat"])
        with patch("tools.download_firmware.open_https") as opener:
            with self.assertRaisesRegex(ValueError, "tracked"):
                self.fetch()
            opener.assert_not_called()


if __name__ == "__main__":
    unittest.main()
