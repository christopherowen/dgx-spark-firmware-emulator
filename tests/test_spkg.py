"""The SPKG containers: the parser, checked on synthetic packages and on a local capsule's structure.

The capsule's `STMM`, `FLASH` and `ESPI` components are signed packages around an Arm FF-A partition
manifest. The parser is exercised here on an original synthetic package and device tree. When a
prepared capsule is present locally, its packages are checked for structure only: values read out of
vendor images are not recorded in this repository.
"""

import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import container, spkg  # noqa: E402
from tools.spark_image import capsule_payload  # noqa: E402

CAPSULE = ROOT / "firmware" / "prepared" / "2.158.15" / "socfw.cap"


def _fdt(root: dict[str, bytes], children: dict[str, dict[str, bytes]]) -> bytes:
    """Build a minimal flattened device tree: one root node with properties and child nodes."""
    strings = bytearray()
    offsets: dict[str, int] = {}

    def name_offset(name: str) -> int:
        if name not in offsets:
            offsets[name] = len(strings)
            strings.extend(name.encode() + b"\x00")
        return offsets[name]

    def pad(data: bytearray) -> None:
        data.extend(b"\x00" * (-len(data) % 4))

    def node(body: bytearray, name: str, props: dict[str, bytes]) -> None:
        body.extend(struct.pack(">I", 1) + name.encode() + b"\x00")
        pad(body)
        for prop, value in props.items():
            body.extend(struct.pack(">III", 3, len(value), name_offset(prop)) + value)
            pad(body)

    body = bytearray()
    node(body, "", root)
    for child, props in children.items():
        node(body, child, props)
        body.extend(struct.pack(">I", 2))
    body.extend(struct.pack(">II", 2, 9))
    header = 40
    struct_at = header
    strings_at = struct_at + len(body)
    total = strings_at + len(strings)
    return struct.pack(">10I", 0xD00DFEED, total, struct_at, strings_at, header, 17, 16, 0,
                       len(strings), len(body)) + bytes(body) + bytes(strings)


def _package(manifest: bytes, image: bytes) -> bytes:
    data = bytearray(0x2000 + len(image))
    data[0:0x20] = (b"SPKG" + struct.pack("<I", 2) + struct.pack("<II", 0x1000, len(manifest))
                    + struct.pack("<II", 0x2000, len(image)) + b"\x00" * 8)
    data[0x1000:0x1000 + len(manifest)] = manifest
    data[0x2000:] = image
    return bytes(data)


class SpkgParserTests(unittest.TestCase):
    def test_a_synthetic_package_and_manifest_read_back(self):
        manifest = _fdt({"description": b"Synthetic Service\x00", "id": struct.pack(">I", 0x8123)},
                        {"device-regions/uart": {"base-address": struct.pack(">Q", 0x1000_0000),
                                                 "attributes": struct.pack(">I", 3)}})
        body = _package(manifest, b"\xaa" * 0x40)
        package = spkg.package(body)
        self.assertEqual(package.extents(), {"manifest": (0x1000, len(manifest)),
                                             "image": (0x2000, 0x40)})
        parsed = spkg.manifest(body[0x1000:0x1000 + len(manifest)])
        self.assertEqual(parsed.properties["description"], "Synthetic Service")
        self.assertEqual(parsed.properties["id"], 0x8123)
        self.assertEqual(len(parsed.regions), 1)
        self.assertEqual(parsed.regions[0]["base-address"], 0x1000_0000,
                         "an address cell stays a number even when its bytes are printable")
        self.assertEqual(parsed.regions[0]["attributes"], 3)

    def test_a_wrong_magic_is_refused(self):
        with self.assertRaises(spkg.SpkgError):
            spkg.package(b"NOPE" + b"\x00" * 64)

    def test_an_extent_past_the_component_is_refused(self):
        data = bytearray(b"SPKG" + (2).to_bytes(4, "little") + b"\x00" * 0x30)
        data[8:16] = (0x1000).to_bytes(4, "little") + (0x10000).to_bytes(4, "little")
        with self.assertRaises(spkg.SpkgError) as caught:
            spkg.package(bytes(data))
        self.assertIn("past the component", str(caught.exception))

    def test_a_truncated_package_is_refused(self):
        data = b"SPKG" + (2).to_bytes(4, "little") + (0x1000).to_bytes(4, "little") \
               + (0x672).to_bytes(4, "little") + (0x2000).to_bytes(4, "little") + b"\x00" * 8
        with self.assertRaises(spkg.SpkgError):
            spkg.package(data)

    def test_an_unsupported_version_is_refused(self):
        data = b"SPKG" + (3).to_bytes(4, "little") + b"\x00" * 0x40
        with self.assertRaises(spkg.SpkgError) as caught:
            spkg.package(data)
        self.assertIn("version", str(caught.exception))

    def test_a_manifest_that_names_nothing_is_refused(self):
        with self.assertRaises(spkg.SpkgError):
            spkg.manifest(b"\x00" * 64)

    @unittest.skipUnless(CAPSULE.is_file(), "the prepared capsule is not present")
    def test_the_local_capsule_packages_parse(self):
        payload = capsule_payload(CAPSULE.read_bytes())
        for name in ("STMM", "FLASH", "ESPI"):
            data = container.extract(payload, name)
            body = data[container.body_offset(data):]
            package = spkg.package(body)
            self.assertLessEqual(package.manifest_offset + package.manifest_length,
                                 package.image_offset, f"{name}: the image does not overlap the manifest")
            manifest = spkg.manifest(body[package.manifest_offset:
                                          package.manifest_offset + package.manifest_length])
            self.assertTrue(manifest.properties, f"{name}: the manifest names something")


if __name__ == "__main__":
    unittest.main()
