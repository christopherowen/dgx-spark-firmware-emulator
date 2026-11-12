#!/usr/bin/env python3
"""Run targeted mutations against the converted probe and research-record oracles.

The checkout is never mutated. Each existing test first passes unmodified, then
runs against an isolated source/record copy. Only assertion failures kill a mutant;
compile errors, crashes, skips, and unrelated exceptions do not count as evidence.
"""

import argparse
from contextlib import ExitStack
import importlib
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT))

# test id, patched module global, input path, nested data key (or source text), replacement
MUTATIONS = [
    ("test_probe_allowlist.AllowlistTests.test_the_source_states_the_two_sets", "SOURCE", "tests/fixtures/fan_probe.c",
     "|| (command) == 0x06000706", "|| 0"),
    ("test_probe_allowlist.AllowlistTests.test_the_read_only_build_carries_no_write_opcode", "SOURCE", "tests/fixtures/fan_probe.c",
     "#define ALLOWED_WRITE(command) 0", "#define ALLOWED_WRITE(command) ((command) >= 0x06000790 && (command) <= 0x06000793)"),
    ("test_probe_allowlist.AllowlistTests.test_the_full_build_still_offers_the_fan_sequence", "SOURCE", "tests/fixtures/fan_probe.c",
     "#define ALLOWED_WRITE(command) ((command) >= 0x06000790 && (command) <= 0x06000793)", "#define ALLOWED_WRITE(command) 0"),
    ("test_dp_self.DevicePathSourceTests.test_the_probe_states_the_two_offsets_it_depends_on", "SOURCE", "tests/fixtures/dp_self.c",
     "*(u64 *)boot != 0x56524553544f4f42ULL", "0"),
    ("test_dp_self.DevicePathSourceTests.test_the_probe_states_the_two_offsets_it_depends_on", "SOURCE", "tests/fixtures/dp_self.c",
     "system_table + 0x60", "system_table + 0x58"),
    ("test_fs_census.FileSystemCensusTests.test_the_probe_checks_the_boot_services_signature_first", "SOURCE", "tests/fixtures/fs_census.c",
     "*(u64 *)boot != 0x56524553544f4f42ULL", "0"),
    ("test_fs_census.FileSystemCensusTests.test_the_probe_checks_the_boot_services_signature_first", "SOURCE", "tests/fixtures/fs_census.c",
     "system_table + 0x60", "system_table + 0x58"),
    ("test_firmware_catalog.FirmwareCatalogTests.test_the_answer_is_stated_with_a_date", "RECORD", "research/firmware-catalog.json",
     ("components", "com.nvidia.dgx.spark.socfw.firmware", "releases", 0, "date"), "2026-08-31"),
    ("test_firmware_catalog.FirmwareCatalogTests.test_the_dell_capsule_is_recorded_as_a_different_product", "RECORD", "research/firmware-catalog.json",
     ("local", "spark-soc-4.1.0.cab", "component"), "com.nvidia.dgx.spark.socfw.firmware"),
]


def run_test(test_id):
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromName(test_id))
    return result, stream.getvalue()


def check_mutations(output):
    build = ROOT / "build" / "oracle-mutations"
    build.mkdir(parents=True, exist_ok=True)
    entries = []
    baselines = {}
    for test_id, attribute, relative, key, replacement in MUTATIONS:
        if test_id not in baselines:
            result, log = run_test(test_id)
            baselines[test_id] = result.wasSuccessful() and not result.skipped and result.testsRun == 1
            entries.append({"test": test_id, "kind": "baseline", "passed": baselines[test_id], "log": log})
        with tempfile.TemporaryDirectory(dir=build) as directory, ExitStack() as stack:
            work = Path(directory)
            original = ROOT / relative
            mutant = work / original.name
            if attribute == "RESEARCH":
                research = work / "research"
                shutil.copytree(ROOT / "research", research)
                mutant = research / original.name
            if attribute == "SOURCE":
                text = original.read_text()
                if text.count(key) != 1:
                    raise ValueError(f"mutation anchor is not unique: {relative}: {key}")
                mutant.write_text(text.replace(key, replacement))
            else:
                data = json.loads(original.read_text())
                parent = data
                for part in key[:-1]:
                    parent = parent[part]
                parent[key[-1]] = replacement
                mutant.write_text(json.dumps(data))
            module = importlib.import_module(test_id.split(".")[0])
            stack.enter_context(patch.object(module, attribute, mutant.parent if attribute == "RESEARCH" else mutant))
            result, log = run_test(test_id)
            killed = baselines[test_id] and bool(result.failures) and not result.errors and not result.skipped
            entries.append({"test": test_id, "kind": "mutation", "input": relative,
                            "key": key, "replacement": replacement, "killed": killed, "log": log})
            print(f"{'KILLED' if killed else 'NOT KILLED'} {test_id}: {key}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(entries, indent=2) + "\n")
    killed = sum(entry.get("killed", False) for entry in entries)
    print(f"{killed}/{len(MUTATIONS)} mutants killed; {len(baselines)} unchanged test IDs exercised; {output}")
    return 0 if killed == len(MUTATIONS) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="local JSON evidence path")
    sys.exit(check_mutations(parser.parse_args().output.resolve()))
