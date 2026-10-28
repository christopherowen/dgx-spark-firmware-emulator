"""Exercise original NIC DMA and backend I/O without any vendor firmware."""

import os
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import time
import unittest
import zlib

from tools.run_spark import Qmp, connect_unix

OWN, EOR, FS, LS = (1 << n for n in (31, 30, 29, 28))


@unittest.skipUnless(os.environ.get("SPARK_QEMU"), "set SPARK_QEMU for NIC integration")
class NicIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        # The VM must run for QEMU's network queues to deliver packets.
        # Execute only our own single-instruction idle loop, never firmware.
        idle = root / "idle.bin"
        idle.write_bytes(struct.pack("<I", 0x14000000))
        listener = socket.socket()
        self.addCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(5)
        command = [os.environ["SPARK_QEMU"], "-M", "virt,spark-fw-lab=on,gic-version=3,virtualization=on",
                   "-cpu", "max", "-m", "2G", "-S", "-nodefaults", "-display", "none",
                   "-serial", "none", "-monitor", "none", "-qtest", f"unix:{root}/qtest,server=on,wait=off",
                   "-qmp", f"unix:{root}/qmp,server=on,wait=off",
                   "-device", f"loader,file={idle},addr=0x80000000,cpu-num=0,force-raw=on",
                   "-qtest-log", "/dev/null", "-netdev", f"socket,id=nic,connect=127.0.0.1:{listener.getsockname()[1]}",
                   "-device", "spark-rtl8168,netdev=nic,addr=1,mac=52:54:00:12:34:56"]
        self.process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.addCleanup(self.stop)
        self.backend, _ = listener.accept()
        self.addCleanup(self.backend.close)
        self.backend.settimeout(2)
        self.control = connect_unix(root / "qtest", self.process)
        self.addCleanup(self.control.close)
        self.stream = self.control.makefile("rb")
        self.addCleanup(self.stream.close)
        self.base = 0x60000000
        self.write(0xf300100010 + 8, self.base | 4, 4)  # BAR2, 64-bit MMIO
        self.write(0xf30010001c, 0, 4)
        self.write(0xf300100004, 6, 2)  # memory decoding + bus mastering
        self.assertEqual(self.read(0xf300100000, 4), 0x816810ec)
        self.assertEqual(self.read(0xf300100100, 4), 0xffffffff)  # no devfn spill
        self.assertEqual(self.read(self.base, 4), 0x12005452)
        qmp_socket = connect_unix(root / "qmp", self.process)
        self.addCleanup(qmp_socket.close)
        Qmp(qmp_socket).request("cont")

    def stop(self):
        self.process.terminate()
        self.process.communicate(timeout=5)

    def request(self, command):
        self.control.sendall(command.encode() + b"\n")
        response = self.stream.readline().decode().strip()
        self.assertTrue(response.startswith("OK"), response)
        return response[2:].strip()

    def write(self, address, value, size):
        self.request(f"write{ {1: 'b', 2: 'w', 4: 'l'}[size]} {address:#x} {value:#x}")

    def read(self, address, size):
        return int(self.request(f"read{ {1: 'b', 2: 'w', 4: 'l'}[size]} {address:#x}"), 16)

    def memory_write(self, address, data):
        self.request(f"write {address:#x} {len(data)} 0x{data.hex()}")

    def memory_read(self, address, size):
        return bytes.fromhex(self.request(f"read {address:#x} {size}").removeprefix("0x"))

    def wait_status(self, mask):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if self.read(self.base + 0x3e, 2) & mask:
                return
            time.sleep(.005)
        self.fail(f"NIC status {mask:#x} never arrived")

    def test_indirect_phy_eri_and_csi_reads_reflect_writes(self):
        self.write(self.base + 0x60, 0x80040000 | 0x1234, 4)
        self.assertEqual(self.read(self.base + 0x60, 4) >> 31, 0)
        self.write(self.base + 0x60, 4 << 16, 4)
        self.assertEqual(self.read(self.base + 0x60, 4), 0x80001234)
        self.write(self.base + 0x70, 0x12345678, 4)
        self.write(self.base + 0x74, 0x8000f0e4, 4)
        self.write(self.base + 0x70, 0xaaaabbbb, 4)
        self.write(self.base + 0x74, 0x800050e4, 4)  # byte enables 0 and 2
        self.write(self.base + 0x74, 0xf0e4, 4)
        self.assertEqual(self.read(self.base + 0x74, 4) >> 31, 1)
        self.assertEqual(self.read(self.base + 0x70, 4), 0x12aa56bb)
        self.write(self.base + 0x68, 0xf000, 4)
        self.assertEqual(self.read(self.base + 0x64, 4), 0x816810ec)

    def test_tx_rx_backend_bytes_ownership_wrap_and_w1c_status(self):
        tx, rx, outgoing, incoming = 0x88000000, 0x88001000, 0x88002000, 0x88003000
        packet = bytes.fromhex("525400123456525400abcdef88b5") + b"original synthetic Ethernet test" * 2
        self.memory_write(outgoing, packet)
        self.write(self.base + 0x20, tx, 4)
        self.write(self.base + 0xe4, rx, 4)
        self.write(self.base + 0x44, 2, 4)  # accept own MAC
        self.write(self.base + 0x37, 12, 1)
        for _ in range(2):  # EOR returns each ring to its single descriptor
            self.memory_write(rx, struct.pack("<IIQ", OWN | EOR | 2048, 0, incoming))
            self.memory_write(tx, struct.pack("<IIQ", OWN | EOR | FS | LS | len(packet), 0, outgoing))
            self.write(self.base + 0x38, 0x40, 1)
            frame = self.backend.makefile("rb", buffering=0)
            try:
                length = int.from_bytes(frame.read(4), "big")
                received = bytearray()
                while len(received) < length:
                    chunk = frame.read(length - len(received))
                    self.assertTrue(chunk)
                    received += chunk
                self.assertEqual(received, packet)
            finally:
                frame.close()
            self.assertFalse(self.read(tx, 4) & OWN)
            self.assertTrue(self.read(self.base + 0x3e, 2) & 4)
            self.write(self.base + 0x3e, 0xffff, 2)
            self.backend.sendall(struct.pack("!I", len(packet)) + packet)
            self.wait_status(1)
            self.assertEqual(self.memory_read(incoming, len(packet)), packet)
            self.assertEqual(self.read(incoming + len(packet), 4), zlib.crc32(packet))
            self.assertEqual(self.read(rx, 4), EOR | FS | LS | (len(packet) + 4))
            self.write(self.base + 0x3e, 0xffff, 2)
            self.assertEqual(self.read(self.base + 0x3e, 2), 0)
        # A guest-owned descriptor cannot be overwritten by a new packet.
        self.memory_write(incoming, b"unchanged")
        self.backend.sendall(struct.pack("!I", len(packet)) + packet)
        self.wait_status(0x10)
        self.assertEqual(self.memory_read(incoming, 9), b"unchanged")
        # An undersized RX buffer also cannot be overwritten.
        self.write(self.base + 0x3e, 0xffff, 2)
        self.memory_write(rx, struct.pack("<IIQ", OWN | EOR | 8, 0, incoming))
        self.backend.sendall(struct.pack("!I", len(packet)) + packet)
        self.wait_status(2)
        self.assertEqual(self.memory_read(incoming, 9), b"unchanged")
