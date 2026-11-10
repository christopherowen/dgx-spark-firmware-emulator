"""The optional NVMe namespace, and the NIC it must not disturb.

The profile's PCIe bridge forwards bus 1 to the GPEX root ports: slot 0 is the NIC and slot 2 is
where an attached controller appears. These tests drive an original probe that reads both config
spaces through ECAM and prints one byte for each, so "storage arrived" and "the NIC survived" are
separate assertions.
"""

import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from shutil import which

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "tests" / "fixtures" / "nvme_probe.c"
BASE = 0x80000000


def tool(name):
    found = which(name)
    if found:
        return found
    bundled = ROOT / "build" / "host-tools" / "bin" / name
    if bundled.is_file():
        return str(bundled)
    raise unittest.SkipTest(f"{name} is not available")


def build_probe(directory: Path) -> Path:
    elf, image = directory / "nvme-probe.elf", directory / "nvme-probe.bin"
    subprocess.run([tool("clang"), "--target=aarch64-none-elf", "-O2", "-ffreestanding",
                    "-nostdlib", f"-Wl,-Ttext={BASE:#x}", "-Wl,-e,_start",
                    "-Wl,--build-id=none", "-fuse-ld=lld", str(PROBE), "-o", str(elf)], check=True)
    subprocess.run([tool("llvm-objcopy"), "-O", "binary", str(elf), str(image)], check=True)
    return image


def run(qemu, image, extra, timeout=8):
    machine = "virt,spark-fw-lab=on,gic-version=3,virtualization=on"
    command = [qemu, "-M", machine, "-cpu", "max", "-m", "2G", "-smp", "1", "-nodefaults",
               "-display", "none", "-monitor", "none", "-serial", "stdio", "-nic", "none",
               *extra, "-device", f"loader,file={image},addr={BASE:#x},cpu-num=0,force-raw=on"]
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=timeout, check=False)
        return result.stdout.decode("utf-8", "replace")
    except subprocess.TimeoutExpired as expired:
        return (expired.output or b"").decode("utf-8", "replace")


@unittest.skipUnless(os.environ.get("SPARK_QEMU"), "set SPARK_QEMU to run the profile")
class StorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.image = build_probe(self.root)
        self.disk = self.root / "namespace.img"
        self.disk.write_bytes(bytes(range(256)) * 4096)
        self.qemu = os.environ["SPARK_QEMU"]

    def storage(self, name="SPARKLAB01"):
        return ["-drive", f"if=none,id=nvme0,file={self.disk},format=raw,snapshot=on",
                "-device", f"nvme,bus=pcie.0,addr=2,drive=nvme0,serial={name}"]

    def test_without_storage_only_the_nic_answers(self):
        self.assertEqual(run(self.qemu, self.image, ["-device", "spark-rtl8168,addr=1"])[:2], "sN")

    def test_storage_appears_on_the_second_root_port(self):
        output = run(self.qemu, self.image,
                     [*self.storage(), "-device", "spark-rtl8168,addr=1"])
        self.assertEqual(output[:2], "SN", output[:40])

    def test_the_source_image_is_not_written_by_a_boot(self):
        before = hashlib.sha256(self.disk.read_bytes()).hexdigest()
        run(self.qemu, self.image, [*self.storage(), "-device", "spark-rtl8168,addr=1"])
        self.assertEqual(hashlib.sha256(self.disk.read_bytes()).hexdigest(), before,
                         "the snapshot must keep firmware writes away from the named file")


if __name__ == "__main__":
    unittest.main()
