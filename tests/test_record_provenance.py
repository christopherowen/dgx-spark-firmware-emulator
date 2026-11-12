"""Validate research-record provenance data, not the truth of arbitrary English claims.

These documentation lints require every record to name its source and every offset-bearing
record to name its coordinate system. Records here are upstream references only: values read
out of vendor images stay in the ignored `firmware/` directory.
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

RESEARCH = ROOT / "research"

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
