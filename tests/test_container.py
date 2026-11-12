"""The capsule's component map: parse it, extract what is there, and say what is not.

The SF_BOOT payload carries a table — one entry per part of the device's firmware, with the
size and the offset it lives at on the flash part. It is a *map*, not a container of
payloads: some regions carry data and some are erased in the shipped capsule. Both facts
matter, and both are asserted here, because "extract the eSPI service" is answerable only
if a reader can see which regions are populated.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.container import (Component, ContainerError, declared_header, extract,  # noqa: E402
                             header_position, resolve, self_described_extent, table)
from tools.spark_image import capsule_payload  # noqa: E402

PREPARED = ROOT / "firmware" / "prepared" / "2.155.11" / "socfw.cap"


def synthetic(entries) -> bytes:
    """A minimal payload with the same header shape, for the parts that need no vendor bytes."""
    import struct

    header = b"AAAA" + struct.pack("<II", 1, 0x10000) + b"\x00" * 4
    body = b""
    for name, size, offset in entries:
        body += (b"BBBB" + struct.pack("<I", 7) + name.encode().ljust(8, b"\x00")
                 + struct.pack("<IIII", size, offset, offset + size, 0))
    payload = bytearray(0x20000)
    payload[0:len(header)] = header
    payload[0x10:0x10 + len(body)] = body
    return bytes(payload)


class ContainerTests(unittest.TestCase):
    def test_a_synthetic_table_round_trips(self):
        payload = bytearray(synthetic([("ALPHA", 0x100, 0x1000), ("BETA", 0x200, 0x2000)]))
        payload[0x1000:0x1100] = b"\xaa" * 0x100
        payload[0x2000:0x2200] = b"data"
        components = table(bytes(payload))
        self.assertEqual([c.name for c in components], ["ALPHA", "BETA"])
        self.assertEqual(extract(bytes(payload), "ALPHA"), b"\xaa" * 0x100)
        self.assertTrue(extract(bytes(payload), "BETA").startswith(b"data"))

    def test_a_table_that_overlaps_itself_is_refused(self):
        payload = bytearray(synthetic([("ALPHA", 0x2000, 0x1000)]))
        import struct
        struct.pack_into("<I", payload, 0x10 + 16 + 8, 0x500)   # next offset inside the entry
        with self.assertRaises(ContainerError):
            table(bytes(payload))

    def test_an_unknown_name_is_refused(self):
        with self.assertRaises(ContainerError):
            extract(synthetic([("ALPHA", 0x100, 0x1000)]), "GAMMA")

    def test_a_payload_without_a_table_is_refused(self):
        with self.assertRaises(ContainerError):
            table(b"\xff" * 0x1000)


@unittest.skipUnless(PREPARED.is_file(), "the 2.155.11 capsule is not prepared")
def relocated(header_at: int, entries) -> bytes:
    """The same table at an arbitrary header position, with a decoy at the naive slice's offsets.

    The base is the *header's* position, so an artifact whose header moves must still extract the
    same bytes. The decoy is what the old code would have returned: FF-free garbage, which is why
    the defect survived — it did not look empty, it looked like other firmware.
    """
    import struct

    need = header_at + 0x10 + 32 * len(entries) + max(offset + size for _, size, offset in entries)
    payload = bytearray(max(0x30000, need))
    payload[:] = b"\xff" * len(payload)
    payload[header_at:header_at + 12] = b"AAAA" + struct.pack("<II", 1, 0x10000)
    cursor = header_at + 0x10
    for name, size, offset in entries:
        payload[cursor:cursor + 32] = (b"BBBB" + struct.pack("<I", 7)
                                       + name.encode().ljust(8, b"\x00")
                                       + struct.pack("<IIII", size, offset, offset + size, 0))
        cursor += 32
        payload[header_at + offset:header_at + offset + size] = bytes([0x40 + len(name)]) * size
        # exactly `size` bytes, or the bytearray would shrink and shift the header
        payload[offset:offset + size] = (b"DECOY" * (size // 5 + 1))[:size]
    return bytes(payload)


class BaseTests(unittest.TestCase):
    def test_the_declaration_and_the_magic_agree(self):
        """`BRLYT` + the pointer at 0x210 is the image's own statement of the base."""
        payload = relocated(0x1234, [("ALPHA", 0x100, 0x100)])
        self.assertIsNone(declared_header(payload), "the synthetic has no declaration")
        declared = bytearray(payload)
        declared[0x200:0x208] = b"BRLYT\x00\x00\x00"
        declared[0x210:0x214] = (0x1234).to_bytes(4, "little")
        self.assertEqual(declared_header(bytes(declared)), 0x1234)
        self.assertEqual(header_position(bytes(declared)), 0x1234)

    def test_a_declaration_pointing_at_nothing_is_an_error(self):
        payload = bytearray(relocated(0x1234, [("ALPHA", 0x100, 0x100)]))
        payload[0x200:0x208] = b"BRLYT\x00\x00\x00"
        payload[0x210:0x214] = (0x900).to_bytes(4, "little")
        with self.assertRaises(ContainerError) as caught:
            header_position(bytes(payload))
        self.assertIn("declares its header", str(caught.exception))

    def test_the_header_position_is_the_base(self):
        for header_at in (0x1000, 0x1234, 0x203000):
            payload = relocated(header_at, [("ALPHA", 0x100, 0x100), ("BETA", 0x200, 0x200)])
            self.assertEqual(header_position(payload), header_at)
            self.assertEqual(len(extract(payload, "ALPHA")), 0x100)
            self.assertNotIn(b"DECOY", extract(payload, "ALPHA"),
                             "the decoy at the naive offset is not the component")
            self.assertEqual(extract(payload, "ALPHA")[0], 0x40 + len("ALPHA"))

    def test_the_naive_slice_is_available_but_only_on_request(self):
        payload = relocated(0x1234, [("ALPHA", 0x100, 0x100)])
        self.assertIn(b"DECOY", extract(payload, "ALPHA", base=0))
        self.assertEqual(resolve(payload, "ALPHA"), (0x1234 + 0x100, 0x1234 + 0x200))

    def test_two_headers_are_ambiguous_not_overwritten(self):
        import struct

        payload = bytearray(relocated(0x1234, [("ALPHA", 0x100, 0x100)]))
        second = 0x5000
        payload[second:second + 12] = b"AAAA" + struct.pack("<II", 1, 0x10000)
        with self.assertRaises(ContainerError) as caught:
            header_position(bytes(payload))
        self.assertIn("ambiguous", str(caught.exception))

    def test_an_entry_inside_its_predecessor_is_refused(self):
        payload = relocated(0x1000, [("ALPHA", 0x200, 0x100), ("BETA", 0x100, 0x200)])
        with self.assertRaises(ContainerError) as caught:
            table(payload)
        self.assertIn("inside", str(caught.exception))

    def test_a_negative_base_is_refused_rather_than_slicing_from_the_end(self):
        payload = relocated(0x1000, [("ALPHA", 0x100, 0x100)])
        with self.assertRaises(ContainerError) as caught:
            resolve(payload, "ALPHA", base=-1)
        self.assertIn("negative", str(caught.exception))

    def test_two_components_with_one_name_are_refused(self):
        payload = relocated(0x1000, [("ALPHA", 0x100, 0x100), ("ALPHA", 0x100, 0x200)])
        with self.assertRaises(ContainerError) as caught:
            table(payload)
        self.assertIn("share a name", str(caught.exception))


PREPARED_DIR = ROOT / "firmware" / "prepared"
# A clean checkout has no `firmware/prepared` at all — the capsules are vendor bytes and are not
# committed — so the listing has to survive the directory being absent, or importing this module fails.
CAPSULES = ({version.name: version / "socfw.cap" for version in PREPARED_DIR.iterdir()
             if (version / "socfw.cap").is_file()} if PREPARED_DIR.is_dir() else {})


@unittest.skipUnless(len(CAPSULES) > 1, "fewer than two prepared capsules are present")
class EveryCapsuleTests(unittest.TestCase):
    """The base is a rule, not a fact about one release: check each capsule the tree holds."""

    def test_each_capsule_yields_the_image_its_version_pins(self):
        import hashlib
        import lzma

        from tools.spark_image import VERSIONS, capsule_payload

        signature = b"MMM\x00\x00\x02\x00\x00FILEINFO"
        for version, path in sorted(CAPSULES.items()):
            with self.subTest(version=version):
                payload = capsule_payload(path.read_bytes())
                self.assertEqual(header_position(payload), 0x203000, "header position")
                component = extract(payload, "UEFI")
                at = component.find(signature)
                self.assertEqual(component.count(signature), 1)
                decoder = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE,
                                                memlimit=128 * 1024 * 1024)
                image = decoder.decompress(component[at + 56:])
                self.assertTrue(decoder.eof)
                self.assertLessEqual(len(decoder.unused_data), 4, "stream ends at the component edge")
                self.assertEqual(hashlib.sha256(image).hexdigest(), VERSIONS[version]["uefi"])


PART_IMAGE = Path("/tmp/usbimg.customer/spirom/dgx_spark_ec_fused.bin")


@unittest.skipUnless(PART_IMAGE.is_file(), "the recovery media's part image is not present")
class PartImageTests(unittest.TestCase):
    """A third artifact class: the whole-part image. Same rule, base derived per slot.

    The part holds two slots, so a slot has to be delimited before its header is unique — which is
    exactly what the ambiguity guard is for: the unbounded slot 0 slice sees two headers.
    """

    def setUp(self):
        self.image = PART_IMAGE.read_bytes()

    def test_each_slot_resolves_to_the_same_offsets(self):
        for start, end in ((0x100000, 0x2000000), (0x2000000, len(self.image))):
            slot = self.image[start:end]
            self.assertEqual(header_position(slot), 0x203000, "header in slot coordinates")
            self.assertEqual(resolve(slot, "BCT")[0], 0x1b95000)
            self.assertEqual(resolve(slot, "UEFI")[0], 0xdd7000)

    def test_the_bct_sits_where_the_map_says_in_both_slots(self):
        """A raw-window claim of 0x3b95c5f for slot 1 was the capsule's wrapper offset, not this file's."""
        for start, expected in ((0x100000, 0x1c95000), (0x2000000, 0x3b95000)):
            with self.subTest(slot=start):
                slot = self.image[start:]
                offset, _ = resolve(slot, "BCT")
                self.assertEqual(start + offset, expected)
                self.assertEqual(self.image[expected:expected + 4], b"BCT\x00",
                                 "the magic is at the region's own start")

    def test_the_declared_pointer_settles_an_undelimited_slot(self):
        """Slot 0's slice also contains slot 1's header, but each slot declares its own base.

        `BRLYT` at 0x200 plus the pointer at 0x210 is the image stating where its header is, so the
        undelimited slice is not ambiguous — it resolves to 0x203000 from the declaration. Only a
        payload with two headers *and* no declaration is refused (covered in BaseTests).
        """
        slot = self.image[0x100000:]
        self.assertEqual(declared_header(slot), 0x203000)
        self.assertEqual(header_position(slot), 0x203000)


@unittest.skipUnless(PREPARED.is_file(), "the prepared capsule is not present")
class PreparedCapsuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = capsule_payload(PREPARED.read_bytes())
        cls.components = table(cls.payload)

    def test_the_map_names_the_components_this_profile_cares_about(self):
        names = {component.name for component in self.components}
        for wanted in ("BL31", "BL32", "ESPI", "STMM", "UEFI", "FLASH"):
            self.assertIn(wanted, names)

    def test_the_entries_are_ordered_and_bounded(self):
        for component in self.components:
            self.assertLessEqual(component.end, len(self.payload), component.name)
        for earlier, later in zip(self.components, self.components[1:]):
            self.assertLessEqual(earlier.offset, later.offset)

    def test_only_the_two_unused_regions_are_blank(self):
        """Corrected: the map is a map, but almost everything in it is present.

        The earlier reading — BL31, BL32, ESPI, FLASH, SCP and TPM "erased in every release" — was
        taken 0x203000 before the partition area the offsets are relative to. At the corrected slice
        only `DDRFSK` and `MISC` carry no data, and BL31 is 98.4% populated. That is why "the eSPI
        service is not in the capsule" had to be withdrawn: `ESPI` is there, encrypted.
        """
        blank = [c.name for c in self.components
                 if set(extract(self.payload, c.name)) == {0xFF}]
        populated = [c.name for c in self.components if c.name not in blank]
        self.assertEqual(len(self.components), 29)
        self.assertEqual(len(blank) + len(populated), 29)
        self.assertEqual(sorted(blank), ["DDRFSK", "MISC"], "the only regions the capsule leaves blank")
        for name in ("BL31", "BL32", "ESPI", "STMM", "SCP", "TPM", "SSPM"):
            self.assertIn(name, populated, f"{name} is present in this capsule")
        bl31 = extract(self.payload, "BL31")
        self.assertGreater(sum(byte != 0xFF for byte in bl31) / len(bl31), 0.9,
                           "and BL31 is nearly all non-FF, not an erased region")

    def test_every_component_states_its_own_extent(self):
        """The map is not the only witness: each component's length chain must end where it ends.

        Every non-blank component except `BCT` is CBOR-wrapped and carries a descriptor table whose
        last sub-record's offset + header + body equals the component's size. That checks the *end*
        of every range, which the UEFI decode alone cannot do for the other 25.
        """
        checked, blank = 0, []
        for component in table(self.payload):
            data = extract(self.payload, component.name)
            if set(data) == {0xFF}:
                blank.append(component.name)
                continue
            extent = self_described_extent(data)
            if extent is None:
                self.assertEqual(component.name, "BCT", "only the BCT patch is not self-describing")
                continue
            self.assertEqual(extent, len(data), f"{component.name} states its own extent")
            checked += 1
        self.assertEqual(checked, 26, "26 components are self-describing in this capsule")
        self.assertEqual(sorted(blank), ["DDRFSK", "MISC"])

    @unittest.skipUnless(PREPARED.is_file(), "the prepared capsule is not present")
    def test_the_uefi_component_decodes_to_the_image_this_repository_boots(self):
        """The check that proves the base, using bytes that do not come from the table.

        `spark_image.unpack_uefi` scans a whole payload, so its success says nothing about a slice.
        Here the FILEINFO descriptor is taken *inside* the extracted component and must decode to
        the 33554432-byte firmware image whose hash is the pinned one.
        """
        import hashlib
        import lzma

        from tools.spark_image import VERSIONS

        expected = VERSIONS[PREPARED.parent.name]["uefi"]
        signature = b"MMM\x00\x00\x02\x00\x00FILEINFO"
        component = extract(self.payload, "UEFI")
        at = component.find(signature)
        self.assertGreaterEqual(at, 0, "the component carries the compression descriptor")
        self.assertEqual(component.find(signature, at + 1), -1, "exactly one descriptor")
        decoder = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE, memlimit=128 * 1024 * 1024)
        image = decoder.decompress(component[at + 56:], max_length=33554432 + 1)
        self.assertTrue(decoder.eof, "the stream terminates inside the component")
        self.assertLessEqual(len(decoder.unused_data), 4,
                             "the stream ends at the component's edge, not mid-payload: a window "
                             "shifted by a byte would truncate it or leave trailing data")
        self.assertEqual(len(image), 33554432)
        self.assertEqual(image[40:44], b"_FVH")
        self.assertEqual(hashlib.sha256(image).hexdigest(), expected)


if __name__ == "__main__":
    unittest.main()
