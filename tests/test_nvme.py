"""The optional NVMe namespaces, and the NIC they must not disturb.

The profile's bridge model decodes bus 1 by slot: slot 0 is the NIC, which the host bridge keeps at
device 1, and every other slot is itself, so an attached controller appears where it was attached.
Both configuration windows that carry those endpoints are probed — segment 0 at 0xf300000000, which
the firmware walks, and segment 15 at 0x29000000, which the vendor's SSDT enables for a Linux guest.
The probe prints one byte per claim, so "storage arrived", "the NIC survived", "the guest's window
exists" and "a second namespace answers" are separate assertions.
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

    def storage(self, image=None, addr=2, name="SPARKLAB01"):
        return ["-drive", f"if=none,id=nvme{addr},file={image or self.disk},format=raw,snapshot=on",
                "-device", f"nvme,bus=pcie.0,addr={addr},drive=nvme{addr},serial={name}"]

    def test_without_storage_only_the_nic_answers(self):
        output = run(self.qemu, self.image, ["-device", "spark-rtl8168,addr=1"])
        self.assertEqual(output[:5], "NsdUB", output[:40])

    def test_storage_appears_where_the_guest_os_walks(self):
        output = run(self.qemu, self.image,
                     [*self.storage(), "-device", "spark-rtl8168,addr=1"])
        self.assertEqual(output[:4], "NSdU", output[:40])

    def test_the_guest_os_window_has_no_root_port_in_the_model(self):
        """Segment 15's root bus carries the controllers; slot 0 stays empty.

        Measured: a bridge at 0f:00.0 derails the firmware's own boot even with nothing behind it —
        the run stops reaching its PXE option — so the model puts storage on the root bus and models
        no root port at all.
        """
        output = run(self.qemu, self.image,
                     [*self.storage(), "-device", "spark-rtl8168,addr=1"])
        self.assertEqual(output[4:5], "B", output[:40])

    def test_a_second_namespace_answers_on_its_own_slot(self):
        target = self.root / "target.img"
        target.write_bytes(bytes(range(256)) * 512)
        output = run(self.qemu, self.image,
                     [*self.storage(), *self.storage(target, addr=3, name="SPARKLAB02"),
                      "-device", "spark-rtl8168,addr=1"])
        self.assertEqual(output[2:3], "D", output[:40])

    def test_one_namespace_leaves_the_second_slot_empty(self):
        output = run(self.qemu, self.image,
                     [*self.storage(), "-device", "spark-rtl8168,addr=1"])
        self.assertEqual(output[2:3], "d", output[:40])

    def test_no_device_answers_in_both_windows(self):
        """The same controller must not be reachable through segment 0 and segment 15.

        Measured: with both windows serving the endpoints, the firmware's own enumeration read the same
        controller twice and the boot never reached PXE; the trace of that run is 17 reads of slot 2 in
        each window.
        """
        output = run(self.qemu, self.image,
                     [*self.storage(), "-device", "spark-rtl8168,addr=1"])
        self.assertEqual(output[3:4], "U", output[:40])

    def test_the_source_image_is_not_written_by_a_boot(self):
        before = hashlib.sha256(self.disk.read_bytes()).hexdigest()
        run(self.qemu, self.image, [*self.storage(), "-device", "spark-rtl8168,addr=1"])
        self.assertEqual(hashlib.sha256(self.disk.read_bytes()).hexdigest(), before,
                         "the snapshot must keep firmware writes away from the named file")


if __name__ == "__main__":
    unittest.main()
