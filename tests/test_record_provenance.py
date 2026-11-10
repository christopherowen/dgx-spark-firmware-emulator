"""Every record in `research/` must say where its numbers came from.

The extraction defect recurred four times as the same failure: a number outliving the state it
described — an erased component that was present, an entropy measured on a neighbour's bytes, a
coordinate from one wrapper applied to another file. A sentence in a document did not stop it. This
test does: a record that carries measurements must name its artifact, and a record that carries
offsets must name the coordinate space those offsets are in.
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


class ProvenanceTests(unittest.TestCase):
    def test_every_record_states_its_source(self):
        for name, record in records():
            self.assertIn("source", record, f"{name} does not say where its numbers came from")
            source = record["source"]
            self.assertTrue(source, f"{name} has an empty source")

    def test_slice_derived_records_name_the_artifact_and_the_coordinates(self):
        for name, record in records():
            if name not in SLICE_DERIVED:
                continue
            source = record["source"]
            text = source if isinstance(source, str) else json.dumps(source)
            self.assertIn("sha256", text, f"{name} must name the artifact by hash")
            self.assertIn("coordinate", text, f"{name} must name the coordinate space")

    def test_live_observations_say_how_and_where(self):
        for name, record in records():
            if name not in LIVE_OBSERVATION:
                continue
            source = record["source"]
            text = source if isinstance(source, str) else json.dumps(source)
            self.assertIn("read-only", text.lower(), f"{name} must say the reading was read-only")
            self.assertTrue(any(marker in text for marker in ("ACUM", "kernel", "firmware")),
                            f"{name} must name the firmware or kernel it observed")

    def test_acpi_derived_records_say_acpi(self):
        for name, record in records():
            if name not in DSDT_DERIVED:
                continue
            source = record["source"]
            text = source if isinstance(source, str) else json.dumps(source)
            self.assertIn("DSDT", text, f"{name} is derived from the ACPI tables and must say so")

    def test_no_active_erasure_claim_survives(self):
        """The defect's most expensive product was a sentence, not a number: "erased in every release".

        Any surviving claim of that shape must be inside a retraction — a block that says it is
        withdrawn. The test looks for the phrase families rather than one exact sentence, because the
        claim was rewritten four times and each rewrite kept a fragment.
        """
        phrases = ("erased in all", "erases them", "erases most", "not shipped here",
                   "both regions are erased", "is expected to be erased")
        allowed_context = ("retract", "withdrawn", "was wrong", "no longer", "historical")
        for path in list(RESEARCH.glob("*.json")) + list((ROOT / "docs").glob("*.md")):
            text = path.read_text()
            for phrase in phrases:
                start = text.find(phrase)
                while start >= 0:
                    window = text[max(0, start - 600):start + 600].lower()
                    self.assertTrue(any(marker in window for marker in allowed_context),
                                    f"{path.name}: {phrase!r} survives without a retraction around it")
                    start = text.find(phrase, start + 1)

    def test_a_record_that_states_offsets_states_their_space(self):
        for name, record in records():
            if name in ("sources.json", "firmware-catalog.json"):
                continue                      # inventories, not measurements
            text = json.dumps(record)
            if "slot_offset" in text or "file_offset" in text or "base-address" in text:
                self.assertTrue("coordinate" in text or "coordinate_space" in text,
                                f"{name} names offsets without naming their coordinate space")


if __name__ == "__main__":
    unittest.main()
