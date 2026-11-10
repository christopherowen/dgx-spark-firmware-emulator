"""Validate research-record provenance data, not the truth of arbitrary English claims.

These documentation lints require well-formed artifact identities, coordinate enums,
and observation/table metadata. Artifact replay remains in the record-specific tests.
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

RESEARCH = ROOT / "research"

# Records whose numbers are read out of a capsule or part image: they must name the artifact and the
# coordinates. The list is explicit rather than inferred, so adding a slice-derived record is a
# deliberate act that has to be justified here.
SLICE_DERIVED = {"bct-patch.json", "component-bodies.json", "component-crypto.json", "dvfs.json",
                 "spkg-manifest.json", "watchdog-periods.json", "espi.json",
                 "espi-spbm-registers.json"}
# Records taken from a running unit rather than from a file: they have no artifact hash to name, but they
# must say how they were read and which firmware was running.
LIVE_OBSERVATION = {"ffa-partitions-live.json", "cpu-clusters.json"}
# Records derived from decompiled ACPI: they must say so, because the ACPI table is not a slice.
DSDT_DERIVED = {"cppc.json", "fan-interface.json", "spbm-interface.json"}


def records():
    for path in sorted(RESEARCH.glob("*.json")):
        yield path.name, json.loads(path.read_text())

def field_names(value):
    """Walk data keys only: a word mentioned in prose is not an offset field."""
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from field_names(child)
    elif isinstance(value, list):
        for child in value:
            yield from field_names(child)



class ProvenanceTests(unittest.TestCase):
    def test_every_record_states_its_source(self):
        for name, record in records():
            self.assertIn("source", record, f"{name} does not say where its numbers came from")
            source = record["source"]
            self.assertTrue(source, f"{name} has an empty source")

    def test_slice_derived_records_name_the_artifact_and_the_coordinates(self):
        """Documentation lint: require a hash value and a defined coordinate-system enum."""
        for name, record in records():
            if name not in SLICE_DERIVED:
                continue
            source = record["source"]
            self.assertTrue(source["artifact"], name)
            digest = source.get("capsule_sha256", source.get("sha256"))
            self.assertIsInstance(digest, str, name)
            self.assertRegex(digest, r"^[0-9a-f]{64}$", name)
            self.assertIn(source["coordinate_system"], {"slot_image", "component_image"}, name)
            self.assertTrue(source["coordinate_space"], name)

    def test_live_observations_say_how_and_where(self):
        """Documentation lint: require structured read access, interface and firmware identity."""
        for name, record in records():
            if name not in LIVE_OBSERVATION:
                continue
            source = record["source"]
            self.assertEqual(source["access"], "read_only", name)
            self.assertEqual(source["interface"], "sysfs", name)
            self.assertTrue(source["how"], name)
            self.assertIsInstance(source["firmware"], str, name)
            self.assertTrue(source["firmware"], name)

    def test_acpi_derived_records_say_acpi(self):
        """Documentation lint: require a table identifier and its captured artifact hash."""
        for name, record in records():
            if name not in DSDT_DERIVED:
                continue
            source = record["source"]
            self.assertEqual(source["table_id"], "DSDT", name)
            self.assertRegex(source["sha256"], r"^[0-9a-f]{64}$", name)

    def test_no_active_erasure_claim_survives(self):
        """Documentation lint: component records retain non-erased payload evidence.

        This checks recorded bytes, not arbitrary prose or all bytes of a vendor image.
        The component-specific artifact replay test provides that separate check.
        """
        record = json.loads((RESEARCH / "component-bodies.json").read_text())
        for name, component in record["components"].items():
            self.assertGreater(component["body_length"], 0, name)
            first = bytes.fromhex(component["first_bytes"])
            self.assertEqual(len(first), min(8, component["body_length"]), name)
            self.assertNotEqual(first, b"\xff" * len(first), name)

    def test_a_record_that_states_offsets_states_their_space(self):
        """Documentation lint: offset-bearing data must name a supported coordinate system."""
        for name, record in records():
            if name in ("sources.json", "firmware-catalog.json"):
                continue                      # inventories, not measurements
            if {"slot_offset", "file_offset", "base-address"} & set(field_names(record)):
                self.assertIn(record["source"]["coordinate_system"],
                              {"slot_image", "component_image", "capsule_file", "physical_address"},
                              f"{name} names offsets without a coordinate system")


if __name__ == "__main__":
    unittest.main()
