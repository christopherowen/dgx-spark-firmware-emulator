"""Execute original synthetic secure/nonsecure guests through the lab monitor."""

import os
from pathlib import Path
import selectors
import subprocess
import tempfile
import time
import unittest

from tools.build_monitor import ROOT, build_monitor, find_tool


@unittest.skipUnless(os.environ.get("SPARK_QEMU"), "set SPARK_QEMU for monitor integration")
class MonitorIntegrationTests(unittest.TestCase):
    def test_world_switch_fp_tls_trng_discovery_bounds_and_timer_ppi(self):
        self.run_guests("monitor_probe.S", None, b"MONITOR PASS", b"MONITOR FAIL")

    def test_ns_share_retrieve_release_relinquish_reclaim_and_concurrent_flash(self):
        self.run_guests("memory_probe_start.S", "ffa_memory_probe.c", b"MEMORY PASS", b"MEMORY FAIL")

    def run_guests(self, assembly, c_source, passed, failed):
        monitor = build_monitor()
        with tempfile.TemporaryDirectory(dir=ROOT / "build", prefix="monitor-test-") as temporary:
            directory = Path(temporary)
            for name, address, defines in (("secure", 0x93a42000, ["-DSECURE_PROBE"]),
                                           ("nonsecure", 0x80800000, [])):
                obj, elf, binary = [directory / (name + suffix) for suffix in (".o", ".elf", ".bin")]
                subprocess.run([find_tool("clang"), "--target=aarch64-none-elf", *defines, "-c",
                                str(ROOT / "tests/fixtures" / assembly), "-o", str(obj)], check=True)
                objects = [str(obj)]
                if c_source:
                    c_obj = directory / (name + "-c.o")
                    subprocess.run([find_tool("clang"), "--target=aarch64-none-elf", "-O2",
                                    "-ffreestanding", "-fno-builtin", "-fno-stack-protector",
                                    "-mgeneral-regs-only", "-mstrict-align", "-Wall", "-Wextra", "-Werror",
                                    *defines, "-c", str(ROOT / "tests/fixtures" / c_source),
                                    "-o", str(c_obj)], check=True)
                    objects.append(str(c_obj))
                subprocess.run([find_tool("ld.lld"), f"-Ttext={address:#x}", "-e", "_start",
                                *objects, "-o", str(elf)], check=True)
                subprocess.run([find_tool("llvm-objcopy"), "-O", "binary", str(elf), str(binary)], check=True)
            command = [os.environ["SPARK_QEMU"], "-M", "virt,spark-fw-lab=on,gic-version=3,"
                       "virtualization=on,secure=on", "-cpu", "max,sve=off,sme=off,pauth=off",
                       "-m", "2G", "-nodefaults", "-display", "none", "-monitor", "none",
                       "-serial", "stdio", "-nic", "none"]
            for path, address, cpu in ((monitor, 0x80020000, True),
                                       (directory / "secure.bin", 0x93a42000, False),
                                       (directory / "nonsecure.bin", 0x80800000, False)):
                command += ["-device", f"loader,file={path},addr={address:#x},force-raw=on" +
                            (",cpu-num=0" if cpu else "")]
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT)
            output = bytearray()
            try:
                selector = selectors.DefaultSelector()
                selector.register(process.stdout, selectors.EVENT_READ)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and process.poll() is None:
                    if selector.select(.1):
                        output += os.read(process.stdout.fileno(), 4096)
                        if passed in output or failed in output:
                            break
                self.assertIn(passed, output, output.decode(errors="replace"))
                self.assertNotIn(failed, output)
            finally:
                process.terminate()
                process.communicate(timeout=5)
