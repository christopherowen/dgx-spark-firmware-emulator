"""Run an original synthetic AArch64 probe against the built QEMU profile."""

import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.environ.get("SPARK_QEMU"), "set SPARK_QEMU to test the custom QEMU build")
class ProfileIntegrationTests(unittest.TestCase):
    def probe(self, words, *, rot_error_replies=False):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "original-probe.bin"
            image.write_bytes(struct.pack("<" + "I" * len(words), *words))
            machine = "virt,spark-fw-lab=on,gic-version=3,virtualization=on"
            if rot_error_replies:
                machine += ",spark-rot-error-replies=on"
            command = [os.environ["SPARK_QEMU"], "-M", machine,
                       "-cpu", "max", "-m", "2G", "-smp", "1", "-nodefaults",
                       "-display", "none", "-monitor", "none", "-serial", "stdio",
                       "-nic", "none", "-device",
                       f"loader,file={image},addr=0x80000000,cpu-num=0,force-raw=on"]
            try:
                result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, timeout=2, check=False)
                self.fail(f"probe unexpectedly exited: {result.stdout!r}")
            except subprocess.TimeoutExpired as result:
                self.assertEqual(result.output, b"P")

    def test_gpio_aliases_gicv3_id_and_16550_serial_at_spark_addresses(self):
        # Original probe: exercise GPIO readback, SET, and CLEAR aliases,
        # read GICD_PIDR2 at 0x0680ffe8, require architecture 3,
        # then write P (pass) or F (failure) to the 16550 at 0x16a00000.
        # These are our own assembled instructions, not vendor firmware bytes.
        words = [0xd29a6000, 0xf2a20040, 0x52824681, 0xb9000001,
                 0x52800021, 0xb9000401, 0x52800081, 0xb9000801,
                 0xb9400001, 0x52824622, 0x6b02003f, 0x54000161,
                 0xd29ffd00, 0xf2a0d000, 0xb9400001, 0x53041c21,
                 0x71000c3f, 0x540000a1, 0xd2a2d400, 0x52800a01,
                 0xb9000001, 0x14000000, 0xd2a2d400, 0x528008c1,
                 0xb9000001, 0x17fffffc]
        self.probe(words)

    def test_uart_interrupt_uses_spcr_gsi_878(self):
        # Own instructions: enable 16550 TX-empty IRQ, require GICD_ISPENDR
        # bit 878 at 0x0680026c, disable IRQ, require the bit to clear, print P.
        self.probe([0xd2a2d400, 0x52800041, 0xb9000401, 0xd2804d82,
                    0xf2a0d002, 0xb9400043, 0x36700103, 0x52800001,
                    0xb9000401, 0xb9400043, 0x37700083, 0x52800a01,
                    0xb9000001, 0x14000000, 0x528008c1, 0xb9000001,
                    0x17fffffd])

    def test_eint_software_pending_aggregates_banks_on_gsi_563(self):
        # Own instructions: check reset mask, enable domain/unmask bit 0,
        # set software pending in banks 0 and 1, require parent GIC bit 563.
        # Clear bank 0: parent must remain pending. ACK bank 1: parent clears.
        self.probe([0xd2a24820, 0xd2a25c01, 0xd2804882, 0xf2a0d002,
                    0xb9408003, 0x3100047f, 0x54000321, 0x52800023,
                    0xb9040003, 0xb9010003, 0xb9024003, 0xb9400004,
                    0x7100049f, 0x54000241, 0xb9400044, 0x36980204,
                    0xb9040023, 0xb9010023, 0xb9024023, 0xb9028003,
                    0xb9400004, 0x35000144, 0xb9400044, 0x36980104,
                    0xb9004023, 0xb9400044, 0x379800a4, 0xd2a2d400,
                    0x52800a01, 0xb9000001, 0x14000000, 0xd2a2d400,
                    0x528008c1, 0xb9000001, 0x17fffffc])

    def test_rot_failure_transport_never_returns_service_success(self):
        # Own instructions: require TX-ready, send 0x129 + one payload word,
        # check ACK and RX pending/W1C behavior and reply 0x1029 (status 1,
        # ID 0x29, zero payload words). Send ID 0x55 and require 0x1055 too.
        # Then print P. This tests a failure scenario, not a real ROT service.
        self.probe([0xd2a30020, 0xb9402001, 0x7100043f, 0x54000441,
                    0x52802521, 0xb9001001, 0x52b20001, 0xb9001001,
                    0x52800021, 0xb9003001, 0xb9408002, 0x7100045f,
                    0x54000321, 0xb9009001, 0xb9408002, 0x350002c2,
                    0xb9405002, 0x7100045f, 0x54000261, 0xb9404002,
                    0x52820523, 0x6b03005f, 0x540001e1, 0xb9006001,
                    0xb9405002, 0x35000182, 0x52800aa2, 0xb9001002,
                    0xb9003001, 0xb9404002, 0x52820aa3, 0x6b03005f,
                    0x540000a1, 0xd2a2d400, 0x52800a01, 0xb9000001,
                    0x14000000, 0xd2a2d400, 0x528008c1, 0xb9000001,
                    0x17fffffc], rot_error_replies=True)
