"""The board image: two halves, one device, and no silent truncation."""

import tempfile
import unittest
from pathlib import Path

from tools.board_image import (BoardImage, CODE_OFFSET, DEVICE_SIZE, FV_SIGNATURE_OFFSET,
                               ImageError, report, split)


def half(size: int, marker: bytes) -> bytes:
    blob = bytearray(b"\xff" * size)
    blob[FV_SIGNATURE_OFFSET:FV_SIGNATURE_OFFSET + 4] = b"_FVH"
    blob[0x100:0x100 + len(marker)] = marker
    return bytes(blob)


class BoardImageTests(unittest.TestCase):
    def test_compose_places_each_half_at_its_measured_offset(self):
        store = half(0x2000000, b"STORE")
        code = half(0x2000000, b"CODE")
        image = BoardImage(store=store, code=code).compose()
        self.assertEqual(len(image), DEVICE_SIZE)
        self.assertEqual(image[:0x2000000], store)
        self.assertEqual(image[CODE_OFFSET:], code)
        again = split(image)
        self.assertEqual(again.store, store)
        self.assertEqual(again.code, code)

    def test_compose_rejects_a_half_of_the_wrong_size(self):
        with self.assertRaises(ImageError):
            BoardImage(store=b"\xff" * 0x1000000, code=half(0x2000000, b"CODE")).compose()

    def test_compose_rejects_a_half_that_is_not_a_firmware_volume(self):
        with self.assertRaises(ImageError):
            BoardImage(store=b"\x00" * 0x2000000, code=half(0x2000000, b"CODE")).compose()

    def test_split_rejects_a_device_of_the_wrong_size(self):
        with self.assertRaises(ImageError):
            split(b"\xff" * 0x1000)

    def test_report_names_both_halves(self):
        info = report(BoardImage(store=half(0x2000000, b"STORE"),
                                 code=half(0x2000000, b"CODE")).compose())
        self.assertEqual(info["size"], DEVICE_SIZE)
        self.assertEqual(info["code"]["offset"], CODE_OFFSET)
        self.assertTrue(info["store"]["fv"])
        self.assertTrue(info["code"]["fv"])


if __name__ == "__main__":
    unittest.main()
