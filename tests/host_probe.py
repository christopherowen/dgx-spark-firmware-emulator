"""Compile the real EFI probe with a host-only transport/table fixture."""

from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


@contextmanager
def host_probe(source: Path, harness: str, defines=()):
    compiler = shutil.which("clang")
    if not compiler:
        raise unittest.SkipTest("clang is required for the host probe harness")
    build = ROOT / "build" / "host-probe-tests"
    build.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=build) as directory:
        work = Path(directory)
        shutil.copyfile(source, work / source.name)
        shutil.copyfile(FIXTURES / "fan_smc.h", work / "fan_smc.h")
        shutil.copyfile(FIXTURES / harness, work / harness)
        binary = work / "probe"
        subprocess.run([compiler, "-O2", "-ffreestanding", *[f"-D{item}" for item in defines],
                        str(work / harness), "-o", str(binary)], check=True, timeout=30)

        def invoke(*arguments):
            result = subprocess.run([str(binary), *map(str, arguments)], check=True,
                                    capture_output=True, text=True, timeout=10)
            return json.loads(result.stdout)

        yield invoke
