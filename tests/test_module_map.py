"""Firmware layout: volumes, modules, and matching a RAM window back into one."""

import struct
import unittest
import uuid

from tools.module_map import locate, module_at, modules, volumes

FV_HEADER = 0x48
OUTER_SIZE = 0x2000
INNER_START = 0x1000
INNER_SIZE = 0x1000
MODULE_GUID = uuid.UUID("b13edd38-684c-41ed-a305-d7b7e32497df")
FILLER_GUID = uuid.UUID("d7bb1921-3294-474f-b5e2-5c4ea31b6bdc")


def ffs(guid: uuid.UUID, kind: int, payload: bytes) -> bytes:
    """A real FFS header: Name[16], header checksum, 0xAA file checksum, type, attributes, size[3]."""
    body = bytearray(24 + len(payload))
    body[0:16] = guid.bytes_le
    body[16] = 0x22
    body[17] = 0xAA
    body[18] = kind
    body[19] = 0x00
    body[20:23] = len(body).to_bytes(3, "little")
    body[24:] = payload
    return bytes(body) + b"\xff" * ((-len(body)) % 8)


def volume(size: int, files: bytes) -> bytearray:
    data = bytearray(b"\xff" * size)
    data[0x28:0x2c] = b"_FVH"
    struct.pack_into("<Q", data, 0x20, size)
    struct.pack_into("<H", data, 0x30, FV_HEADER)
    data[FV_HEADER:FV_HEADER + len(files)] = files
    return data


class ModuleMapTests(unittest.TestCase):
    def build(self) -> bytes:
        code = bytes((index * 7) % 251 for index in range(192))
        inner = volume(INNER_SIZE, ffs(MODULE_GUID, 0x07, code))
        outer = volume(OUTER_SIZE, ffs(FILLER_GUID, 0x07, bytes(32)))
        outer[INNER_START:INNER_START + INNER_SIZE] = inner
        return bytes(outer)

    def test_both_volumes_are_found(self):
        self.assertEqual([v.offset for v in volumes(self.build())], [0x0, INNER_START])

    def test_outer_walk_stops_at_the_inner_volume(self):
        image = self.build()
        outer_only = [m for m in modules(image) if m.offset < INNER_START]
        self.assertEqual([m.guid for m in outer_only], [str(FILLER_GUID)])
        self.assertLessEqual(outer_only[0].end, INNER_START)
        inner = [m for m in modules(image) if m.offset >= INNER_START]
        self.assertEqual([m.guid for m in inner], [str(MODULE_GUID)])

    def test_window_matches_its_module(self):
        image = self.build()
        module = next(m for m in modules(image) if m.guid == str(MODULE_GUID))
        window = image[module.offset + 24 + 0x10:module.offset + 24 + 0x50]
        hit = locate(image, window)
        self.assertIsNotNone(hit)
        offset, length, skipped = hit
        self.assertEqual(module_at(image, offset).guid, str(MODULE_GUID))
        self.assertGreaterEqual(skipped, 0)
        self.assertGreaterEqual(length, 24)

    def test_a_random_window_is_not_a_file_header(self):
        image = self.build()
        # the filler payload sits where a naive walker would accept it
        bogus = image[FV_HEADER + 24: FV_HEADER + 48]
        self.assertTrue(all(m.offset != FV_HEADER + 24 for m in modules(image)))
        self.assertIsNone(module_at(image, FV_HEADER + 48 + len(bogus) + 1))

    def test_unknown_window_returns_none(self):
        self.assertIsNone(locate(self.build(), b"\x5a" * 64))


if __name__ == "__main__":
    unittest.main()
