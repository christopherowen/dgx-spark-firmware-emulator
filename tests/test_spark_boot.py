"""Synthetic container, handoff, and debugger tests; no vendor firmware inputs."""

import hashlib
import lzma
import struct
import unittest

from tools.handoff import diagnostic_boot_arguments
from tools.spark_image import FMP_GUID, SOC_GUID, capsule_payload, unpack_uefi


class BootInputTests(unittest.TestCase):
    def test_loader_tag_has_full_fixed_size_and_zero_terminator(self):
        data = diagnostic_boot_arguments()
        self.assertEqual(struct.unpack_from("<II", data), (0x1868, 0x88610005))
        self.assertEqual(len(data), 0x1868 + 8)
        self.assertEqual(data[-8:], bytes(8))
        count = struct.unpack_from("<I", data, 0x78)[0]
        entries = []
        for index in range(count):
            offset = 0x80 + 56 * index
            entries.append((data[offset:offset + 32].rstrip(b"\0"),
                            struct.unpack_from("<QQI", data, offset + 32)))
        self.assertIn((b"MMAP_TINY_UEFI", (0x80000000, 0x8000000, 1)), entries)
        self.assertIn((b"MMIO", (0, 0x40000000, 0)), entries)

    def test_capsule_unwrap_honors_offsets_and_rejects_truncation(self):
        payload = b"SF_BOOT" + bytes(80)
        mss1 = b"MSS1" + struct.pack("<III", 16, 1, 0) + payload
        auth = bytes(8) + struct.pack("<I", 24) + bytes(20)
        image = bytearray(48)
        struct.pack_into("<I", image, 0, 3)
        image[4:20] = SOC_GUID.bytes_le
        struct.pack_into("<II", image, 24, len(auth + mss1), 0)
        body = struct.pack("<IHHQ", 1, 0, 1, 16) + image + auth + mss1
        capsule = FMP_GUID.bytes_le + struct.pack("<III", 32, 0, 32 + len(body)) + bytes(4) + body
        self.assertEqual(capsule_payload(capsule), payload)
        for damaged in (capsule[:-1], capsule[:12], bytes(16) + capsule[16:]):
            with self.subTest(length=len(damaged)), self.assertRaises(ValueError):
                capsule_payload(damaged)

    def test_lzma_extraction_preserves_exact_image_bytes_and_checks_hash(self):
        image = b"Synthetic UEFI fixture" * 100
        descriptor = b"MMM\x00\x00\x02\x00\x00FILEINFO" + bytes(40)
        payload = b"SF_BOOT" + descriptor + lzma.compress(image, format=lzma.FORMAT_ALONE) + b"trailing"
        digest = hashlib.sha256(image).hexdigest()
        self.assertEqual(unpack_uefi(payload, digest), image)
        with self.assertRaisesRegex(ValueError, "checksum"):
            unpack_uefi(payload, "0" * 64)
        with self.assertRaises(ValueError):
            unpack_uefi(payload[:65], digest)
