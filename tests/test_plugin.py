"""The trace plugin: exact counts, and a capped trace that says it is capped.

The lab's 3000-stop sample was made with a copy of this plugin, and the repository could not
reproduce it: raising the hit limit needed the lab checkout, and a capped trace was
indistinguishable from a complete one. These tests drive an original guest that executes one
instruction a known number of times, so the count is exact and the truncation is observable.
"""

import os
import re
import selectors
import time
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.build_plugin import build as build_plugin  # noqa: E402

SOURCE = ROOT / "tests" / "fixtures" / "trace_loop.c"
ITERATIONS = 3200
IMAGE_BASE = 0x80000000
# The loop's decrement and its branch: `subs w8, w8, #1` then `b.ne` back. The test finds them by
# encoding rather than by a fixed address, so a compiler change cannot silently move the watch
# point onto a different instruction.
DECREMENT = 0x51000508
BRANCH_BACK = 0x17FFFFFB


def find_tool(name):
    from shutil import which
    path = which(name) or (ROOT / "build" / "host-tools" / "bin" / name).is_file() and str(
        ROOT / "build" / "host-tools" / "bin" / name)
    if not path:
        raise unittest.SkipTest(f"{name} is not available")
    return path


def build_probe(directory: Path) -> tuple[Path, int]:
    """Compile and link the loop probe at a fixed base, and return it with the watched address."""
    clang, objcopy = find_tool("clang"), find_tool("llvm-objcopy")
    elf, image = directory / "trace-loop.elf", directory / "trace-loop.bin"
    subprocess.run([clang, "--target=aarch64-none-elf", "-O2", "-ffreestanding", "-nostdlib",
                    f"-Wl,-Ttext={IMAGE_BASE:#x}", "-Wl,-e,_start", "-Wl,--build-id=none",
                    "-fuse-ld=lld", str(SOURCE), "-o", str(elf)], check=True)
    subprocess.run([objcopy, "-O", "binary", str(elf), str(image)], check=True)
    words = [int.from_bytes(image.read_bytes()[i:i + 4], "little")
             for i in range(0, image.stat().st_size, 4)]
    for index in range(len(words) - 1):
        if words[index] == DECREMENT and words[index + 1] == BRANCH_BACK:
            return image, IMAGE_BASE + 4 * index
    raise AssertionError("the probe's loop was not found; the fixture or the compiler changed")


def run_with_plugin(image: Path, address: int, limit: int):
    qemu = os.environ.get("SPARK_QEMU")
    if not qemu:
        raise unittest.SkipTest("set SPARK_QEMU to run the profile")
    command = [qemu, "-M", "virt,spark-fw-lab=on,gic-version=3,virtualization=on",
               "-cpu", "max", "-m", "2G", "-smp", "1", "-nodefaults", "-display", "none",
               "-monitor", "none", "-serial", "stdio", "-nic", "none",
               "-plugin", f"{build_plugin()},pc={address:#x},limit={limit}",
               "-device", f"loader,file={image},addr={IMAGE_BASE:#x},cpu-num=0,force-raw=on"]
    # Terminate rather than kill: the plugin's summary is written on QEMU's exit path, and the probe
    # never stops on its own (it prints its marker, then waits). The pipe is read with a selector so
    # a quiet guest cannot block the wait past the deadline.
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT)
    collected = bytearray()
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    try:
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            for key, _ in selector.select(0.2):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    break
                collected += chunk
            if b"TRACE summary" in collected:
                break
        process.terminate()
        try:
            collected += process.communicate(timeout=10)[0] or b""
        except subprocess.TimeoutExpired:
            process.kill()
            collected += process.communicate()[0] or b""
    finally:
        selector.close()
        if process.poll() is None:
            process.kill()
    text = collected.decode("utf-8", "replace")
    summary = re.search(r"TRACE summary pc=0x[0-9a-f]+ hits=(\d+) printed=(\d+) limit=(\d+) "
                        r"censored=(\d)", text)
    return text, summary


@unittest.skipUnless(os.environ.get("SPARK_QEMU"), "set SPARK_QEMU to run the profile")
@unittest.skipUnless(SOURCE.is_file(), "the loop fixture is present")
class TracePluginTests(unittest.TestCase):
    def test_a_complete_trace_counts_every_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            image, address = build_probe(Path(directory))
            text, summary = run_with_plugin(image, address, ITERATIONS * 2)
        self.assertIsNotNone(summary, text[-400:])
        hits, printed, limit, censored = (int(value) for value in summary.groups())
        self.assertEqual((hits, printed, censored), (ITERATIONS, ITERATIONS, 0))
        self.assertIn("P", text, "the probe did not reach its exit marker")

    def test_a_capped_trace_says_so(self):
        with tempfile.TemporaryDirectory() as directory:
            image, address = build_probe(Path(directory))
            text, summary = run_with_plugin(image, address, 3000)
        self.assertIsNotNone(summary, text[-400:])
        hits, printed, limit, censored = (int(value) for value in summary.groups())
        self.assertEqual((hits, printed, limit, censored), (ITERATIONS, 3000, 3000, 1))
        self.assertIn("TRACE capped", text, "a censored trace must announce itself")
        self.assertEqual(text.count("TRACE hit "), 3000)

    def test_no_limit_logs_and_counts_everything(self):
        with tempfile.TemporaryDirectory() as directory:
            image, address = build_probe(Path(directory))
            text, summary = run_with_plugin(image, address, 0)
        self.assertIsNotNone(summary, text[-400:])
        hits, printed, limit, censored = (int(value) for value in summary.groups())
        self.assertEqual((hits, printed, limit, censored), (ITERATIONS, ITERATIONS, 0, 0))
        self.assertEqual(text.count("TRACE hit "), ITERATIONS)


if __name__ == "__main__":
    unittest.main()
