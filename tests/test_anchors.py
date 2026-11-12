"""The string-anchor scanner: `adrp`+`add` resolve to a file offset, so anchors survive a rebuild.

Addresses differ between builds; what is stable is the instruction structure, so the tool reports
offsets and every claim is made against the bytes at hand. The synthetic cases pin the decoding; a local
capsule, when present, is checked for structure only.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import anchors, container, spkg  # noqa: E402
from tools.spark_image import capsule_payload  # noqa: E402

CAPSULE = ROOT / "firmware" / "prepared" / "2.158.15" / "socfw.cap"


def synthetic(image: bytearray, at: int, target: int, register: int = 2) -> None:
    """Write an adrp+add pair at `at` that resolves to `target` (base-independent)."""
    import struct

    page = target & ~0xFFF
    delta = (page - (at & ~0xFFF)) >> 12
    immediate = delta & 0x1FFFFF
    word = 0x90000000 | ((immediate & 0x3) << 29) | ((immediate >> 2) << 5) | register
    struct.pack_into("<I", image, at, word)
    struct.pack_into("<I", image, at + 4, 0x91000000 | ((register) << 5) | register | (target & 0xFFF) << 10)


class AnchorTests(unittest.TestCase):
    def test_a_pair_resolves_to_the_offset_it_addresses(self):
        image = bytearray(0x4000)
        synthetic(image, 0x100, 0x2ABC)
        found = anchors.references(bytes(image))
        self.assertEqual([(r.at, r.target) for r in found], [(0x100, 0x2ABC)])

    def test_the_base_cancels(self):
        """Reading the same pair at a different file offset still yields the same relative target."""
        image = bytearray(0x8000)
        synthetic(image, 0x1040, 0x2ABC)
        first = anchors.references(bytes(image))
        self.assertEqual([r.target for r in first], [0x2ABC])

    def test_an_add_for_another_register_is_not_a_pair(self):
        image = bytearray(0x4000)
        import struct
        synthetic(image, 0x100, 0x2ABC, register=2)
        struct.pack_into("<I", image, 0x104, 0x91000000 | (3 << 5) | 2)      # adds from x3
        self.assertEqual(anchors.references(bytes(image)), [])

    @unittest.skipUnless(CAPSULE.is_file(), "the prepared capsule is not present")
    def test_a_local_flash_image_resolves_its_own_string_references(self):
        payload = capsule_payload(CAPSULE.read_bytes())
        data = container.extract(payload, "FLASH")
        body = data[container.body_offset(data):]
        package = spkg.package(body)
        image = body[package.image_offset:]
        found = anchors.strings(image)
        self.assertTrue(found, "a code image carries strings")
        resolved = [site for offset, _ in found for site in anchors.callers_of(image, offset)]
        self.assertTrue(resolved, "at least one string is referenced by an adrp+add pair")
        for site in resolved:
            self.assertLess(site.at, len(image), "every reference site lies inside the image")


if __name__ == "__main__":
    unittest.main()
