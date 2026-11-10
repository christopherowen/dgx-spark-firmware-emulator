"""The FAT32 image the profile boots the recovery kernel from.

`tools/make_esp.py` writes a volume with nothing but the structures a FAT32 file system needs.
The reader below is written independently of the writer — it parses the boot sector, walks the FAT
and chains clusters — so the test is not the writer agreeing with itself.
"""

import struct
import tempfile
import unittest
from pathlib import Path

from tools.make_esp import build, layout, short_name

SECTOR = 512


def read_volume(image, name):
    """Return the bytes of `name` (8.3, upper case) and the directory's file list."""
    bytes_per_sector = struct.unpack_from("<H", image, 11)[0]
    sectors_per_cluster = image[13]
    reserved = struct.unpack_from("<H", image, 14)[0]
    fats = image[16]
    fat_sectors = struct.unpack_from("<I", image, 36)[0]
    root = struct.unpack_from("<I", image, 44)[0]
    cluster_bytes = bytes_per_sector * sectors_per_cluster
    fat = reserved * bytes_per_sector
    data = (reserved + fats * fat_sectors) * bytes_per_sector

    def entries(cluster):
        offset = data + (cluster - 2) * cluster_bytes
        for index in range(0, cluster_bytes, 32):
            entry = image[offset + index:offset + index + 32]
            if entry[0] == 0x00:
                return
            if entry[0] == 0xe5 or entry[11] & 0x08:
                continue
            stem = entry[0:8].decode("ascii").rstrip()
            suffix = entry[8:11].decode("ascii").rstrip()
            first = struct.unpack_from("<H", image, offset + index + 26)[0] | \
                struct.unpack_from("<H", image, offset + index + 20)[0] << 16
            size = struct.unpack_from("<I", image, offset + index + 28)[0]
            if not stem and not suffix:
                continue                      # the volume label entry carries no file
            yield f"{stem}.{suffix}" if suffix else stem, first, size

    listing = list(entries(root))
    for entry_name, first, size in listing:
        if entry_name != name:
            continue
        out, cluster = bytearray(), first
        while cluster < 0x0ffffff8:
            offset = data + (cluster - 2) * cluster_bytes
            out += image[offset:offset + cluster_bytes]
            cluster = struct.unpack_from("<I", image, fat + cluster * 4)[0] & 0x0fffffff
        return bytes(out[:size]), [entry[0] for entry in listing]
    raise KeyError(f"{name} is not on the volume: {[entry[0] for entry in listing]}")


class MakeEspTests(unittest.TestCase):
    def test_a_file_comes_back_byte_for_byte(self):
        payload = bytes(range(256)) * 97
        image = build([("kernel.bin", payload), ("initrd", b"initrd-bytes")], 4 * 1024 * 1024)
        self.assertEqual(image[510:512], b"\x55\xaa", "the boot sector ends in its signature")
        read_back, listing = read_volume(image, "KERNEL.BIN")
        self.assertEqual(read_back, payload)
        self.assertEqual(sorted(listing), ["INITRD", "KERNEL.BIN"])

    def test_names_become_short_names(self):
        self.assertEqual(short_name("VMLINUZ.IMG"), ("VMLINUZ", "IMG"))
        self.assertEqual(short_name("INITRD"), ("INITRD", ""))
        self.assertEqual(short_name("kernel.img"), ("KERNEL", "IMG"))
        with self.assertRaises(ValueError):
            short_name("...")

    def test_a_fat32_volume_has_at_least_65525_clusters(self):
        """Fewer clusters than that is not a FAT32 volume, and firmware refuses it while mtools
        does not — the exact way an image can be written, checked and still boot nothing."""
        # The layout is solved, so it can be checked at sizes this host cannot hold as an array.
        for size in (4 * 1024 * 1024, 64 * 1024 * 1024, 240 * 1024 * 1024, 4 * 1024 * 1024 * 1024):
            sectors, fat_sectors, clusters = layout(1024, 1, size)
            self.assertGreaterEqual(clusters, 65525, f"{size} bytes")
            self.assertGreaterEqual(sectors * 512, size, "the volume honours the minimum")
            self.assertGreaterEqual(fat_sectors * 512 // 4, clusters + 2, "the FAT describes them")
        # A 32 MB volume is grown until it is FAT32 rather than written as something a driver drops.
        image = build([("A.TXT", b"x")], 32 * 1024 * 1024)
        total = struct.unpack_from("<I", image, 32)[0]
        self.assertGreaterEqual(total, 65525 + 32 + 2 * 512, "a small request grows a valid volume")

    def test_the_volume_is_large_enough_for_its_files(self):
        image = build([("one.bin", b"x" * 300000)], 1024 * 1024)
        read_back, _ = read_volume(image, "ONE.BIN")
        self.assertEqual(len(read_back), 300000)
        # The volume grows for its files, so a small minimum is a floor and not a promise.
        grown = build([("BIG.BIN", b"y" * (2 * 1024 * 1024))], 1024 * 1024)
        read_back, _ = read_volume(grown, "BIG.BIN")
        self.assertEqual(len(read_back), 2 * 1024 * 1024)

    def test_a_written_image_opens_under_mtools_when_available(self):
        from shutil import which
        mdir = which("mdir")
        if not mdir:
            self.skipTest("mtools is not installed")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "esp.img"
            path.write_bytes(build([("note.txt", b"hello")], 1024 * 1024))
            listing = __import__("subprocess").run([mdir, "-i", str(path), "::"],
                                                  capture_output=True, text=True,
                                                  env={"MTOOLS_SKIP_CHECK": "1", "PATH": "/usr/bin:/bin"})
            self.assertIn("NOTE", listing.stdout)


if __name__ == "__main__":
    unittest.main()
