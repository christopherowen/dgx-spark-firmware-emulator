"""The SPKG containers: what the partitions say they are, and the checks that keep it honest.

The capsule's `STMM`, `FLASH` and `ESPI` components are signed packages around an Arm FF-A partition
manifest. Read properly, the manifest names the partition, its id, its load address and the device
regions it owns — which is the only description of the flash and eSPI services the capsule carries.
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import container, spkg  # noqa: E402
from tools.spark_image import capsule_payload  # noqa: E402

RECORD = ROOT / "research" / "spkg-manifest.json"
FAN = ROOT / "research" / "fan-interface.json"
CAPSULE = ROOT / "firmware" / "prepared" / "2.158.15" / "socfw.cap"



class SpkgRecordTests(unittest.TestCase):
    def setUp(self):
        self.record = json.loads(RECORD.read_text())

    def test_the_partitions_name_themselves(self):
        names = {name: entry["partition"]["description"]
                 for name, entry in self.record["components"].items()}
        self.assertEqual(names, {"STMM": "StandaloneMm Firmware",
                                 "FLASH": "Flash Service",
                                 "ESPI": "eSPI Service"})

    def test_each_image_ends_exactly_at_the_body_end(self):
        for name, entry in self.record["components"].items():
            self.assertTrue(entry["image_ends_at_body_end"], f"{name} image extent")
            manifest_offset, manifest_length = entry["package"]["manifest"]
            image_offset, image_length = entry["package"]["image"]
            self.assertLessEqual(manifest_offset + manifest_length, image_offset,
                                 f"{name}: the image does not overlap the manifest")
            self.assertEqual((manifest_offset, image_offset), (0x1000, 0x2000),
                             f"{name}: both extents sit on their own page")

    def test_the_espi_partition_is_the_endpoint_the_fan_methods_use(self):
        """Two records that were derived independently must agree, or one of them is wrong."""
        espi = self.record["components"]["ESPI"]["partition"]
        self.assertEqual(espi["id"], 0x8003)
        fan = json.loads(FAN.read_text())
        self.assertEqual(fan["smc"]["endpoint"], 0x8003,
                         "the ACPI fan path names the same partition id")

    def test_the_flash_service_owns_the_quad_spi_controller(self):
        flash = self.record["components"]["FLASH"]
        nodes = {region["node"]: region for region in flash["regions"]}
        qspi = [region for node, region in nodes.items() if node.endswith("qspi-reg")]
        self.assertTrue(qspi, "the Flash Service names a QSPI register region")
        self.assertEqual(qspi[0]["description"], "qspi-map-regs")
        self.assertEqual(qspi[0]["attributes"], 3, "read/write, as a flash service needs")


class SpkgParserTests(unittest.TestCase):
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
    def test_every_recorded_field_comes_from_the_capsule(self):
        payload = capsule_payload(CAPSULE.read_bytes())
        for name, entry in json.loads(RECORD.read_text())["components"].items():
            data = container.extract(payload, name)
            body_at = container.body_offset(data)          # derived, never assumed
            self.assertEqual(body_at, entry["body_offset"], f"{name} body offset")
            body = data[body_at:]
            package = spkg.package(body)
            self.assertEqual([package.manifest_offset, package.manifest_length],
                             entry["package"]["manifest"], f"{name} manifest extent")
            self.assertEqual([package.image_offset, package.image_length],
                             entry["package"]["image"], f"{name} image extent")
            manifest = spkg.manifest(body[package.manifest_offset:
                                          package.manifest_offset + package.manifest_length])
            for prop, value in entry["partition"].items():
                self.assertEqual(manifest.properties[prop], value, f"{name} {prop}")
            self.assertEqual(len(manifest.regions), len(entry["regions"]), f"{name} region count")


if __name__ == "__main__":
    unittest.main()
