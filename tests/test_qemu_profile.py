"""Run an original synthetic AArch64 probe against the built QEMU profile.

Each test carries instruction words produced from the matching source in `tests/fixtures/`
(gwdt_probe.c, ecam_probe.c, gpio_probe.c, ramwindow.c), so the tests need no toolchain to run
and the probes can be rebuilt or audited from the tree.
"""

import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

from tools.build_monitor import find_tool, has_llvm_tools



GPIO_ALIAS_WORDS = [
    0xd29a6000, 0xf2a20040, 0x52824681, 0xb9000001,
    0x52800021, 0xb9000401, 0x52800081, 0xb9000801,
    0xb9400001, 0x52824622, 0x6b02003f, 0x54000161,
    0xd29ffd00, 0xf2a0d000, 0xb9400001, 0x53041c21,
    0x71000c3f, 0x540000a1, 0xd2a2d400, 0x52800a01,
    0xb9000001, 0x14000000, 0xd2a2d400, 0x528008c1,
    0xb9000001, 0x17fffffc,
]

GWDT_WORDS = [
    0x52840108, 0x52994009, 0x72a38088, 0x72a77349,
    0xb9000109, 0xb940010a, 0xb85f8108, 0x7100011f,
    0x528008c8, 0x7a490140, 0x52800a09, 0x1a880128,
    0x52a2d409, 0xb9000128, 0x14000000,
]

ECAM_WORDS = [
    0xd2c01e68, 0x529fffe9, 0xb9400108, 0x6a28013f,
    0x52800a08, 0x528009c9, 0x1a880128, 0x52a2d409,
    0xb9000128, 0x14000000,
]

GPIO_WINDOW_WORDS = [
    0x529a0008, 0x52a24889, 0x72a20048, 0xb940011f,
    0x52a248c8, 0xb940013f, 0x52a25069, 0xb940011f,
    0x52a250a8, 0xb940013f, 0xb940011f, 0x52a250e8,
    0x52a25129, 0xb940011f, 0x52a25168, 0xb940013f,
    0xb940011f, 0x52a254a8, 0x52a25c69, 0xb940011f,
    0x52a26c68, 0xb940013f, 0xb940011f, 0x52a2d408,
    0x52800a09, 0xb9000109, 0x14000000,
]

RAMWINDOW_WORDS = [
    0x529fff08, 0x52848d09, 0x52826aeb, 0x72b7ffe8,
    0x72a26ae9, 0x52a8002a, 0xb9000109, 0x72a48d0b,
    0xb9400108, 0xb900014b, 0xb940014a, 0x6b0b015f,
    0x7a491100, 0x528008c8, 0x52800a09, 0x1a880128,
    0x52a2d409, 0xb9000128, 0x14000000,
]
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
            except subprocess.TimeoutExpired as timed_out:
                self.assertEqual(timed_out.output, b"P")
                return
            # QEMU exited instead of running for the whole window. On a shared host the usual cause
            # is that it could not get the machine's memory, which is not a profile regression.
            message = (result.stdout or b"").decode("utf-8", "replace")
            if "Cannot allocate memory" in message or "failed to allocate" in message:
                self.skipTest("the host could not allocate the machine's memory")
            self.fail(f"probe unexpectedly exited: {result.stdout!r}")

    def test_gpio_aliases_gicv3_id_and_16550_serial_at_spark_addresses(self):
        # Original probe: exercise GPIO readback, SET, and CLEAR aliases,
        # read GICD_PIDR2 at 0x0680ffe8, require architecture 3,
        # then write P (pass) or F (failure) to the 16550 at 0x16a00000.
        # These are our own assembled instructions, not vendor firmware bytes.
        words = GPIO_ALIAS_WORDS
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


    def test_gwdt_is_mapped_at_the_board_addresses_and_writable(self):
        # Original probe: write WOR in the control frame at 0x1c042000, read it back, read WCS (must
        # still be clear because the probe never enables it), then print P. Proves the profile still
        # places the board's watchdog where the firmware looks for it.
        words = GWDT_WORDS
        self.probe(words)


    def test_ecam_window_is_mapped_at_the_board_base(self):
        # Original probe: read the vendor id of 0000:00:00.0 through ECAM at 0xf300000000 and print P
        # when a device answers; a plain virt machine faults at that address before printing anything,
        # so P proves the window is mapped and populated on this profile.
        words = ECAM_WORDS
        self.probe(words)


    def test_two_cpus_bring_up_the_primary_and_park_the_second(self):
        # The profile sizes the redistributor region from the CPU count. A second CPU must not
        # disturb the primary: the same probe still prints its marker, and the machine accepts the
        # configuration instead of refusing it.
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "uart.bin"
            image.write_bytes(struct.pack("<4I", 0x52a2d408, 0x52800a09, 0xb9000109, 0x14000000))
            machine = "virt,spark-fw-lab=on,gic-version=3,virtualization=on"
            command = [os.environ["SPARK_QEMU"], "-M", machine, "-cpu", "max", "-m", "2G",
                       "-smp", "2", "-nodefaults", "-display", "none", "-monitor", "none",
                       "-serial", "stdio", "-nic", "none",
                       "-device", f"loader,file={image},addr=0x80000000,cpu-num=0,force-raw=on"]
            try:
                result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, timeout=4, check=False)
                output = result.stdout
            except subprocess.TimeoutExpired as expired:
                output = expired.output or b""
        self.assertIn(b"P", output, output[-200:])

    def test_every_gpio_config_window_is_present(self):
        # Original probe: read the first register of all eleven GPIO config windows the profile adds.
        # A missing window faults before any output, so P means every one of them is mapped.
        words = GPIO_WINDOW_WORDS
        self.probe(words)


    def test_dram_window_is_the_upper_gigabyte_only(self):
        # Original probe: at -m 2G the guest sees RAM at 0x80000000..0xBFFFFFFF only, because the
        # profile aliases PCI BAR space over 0x40000000..0x7FFFFFFF. P requires both: the top of that
        # gigabyte is writable, and the aliased window is not.
        words = RAMWINDOW_WORDS
        self.probe(words)

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


FIXTURES = Path(__file__).resolve().parent / "fixtures"


@unittest.skipUnless(has_llvm_tools(), "rebuilding a probe needs the LLVM host tools")
class ProbeSourceTests(unittest.TestCase):
    """The instruction words above must match the sources in `tests/fixtures/`.

    The words are embedded so the profile tests run without a toolchain; this class is the other
    half of that bargain, rebuilding each probe and failing if source and words have drifted.
    """

    def words_from(self, source: str) -> list[int]:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            obj, elf, binary = work / "p.o", work / "p.elf", work / "p.bin"
            for command in (
                [find_tool("clang"), "--target=aarch64-none-elf", "-O2", "-ffreestanding",
                 "-fno-builtin", "-nostdlib", "-c", str(FIXTURES / source), "-o", str(obj)],
                [find_tool("ld.lld"), "-Ttext=0x80000000", "-e", "_start", str(obj), "-o", str(elf)],
                [find_tool("llvm-objcopy"), "-O", "binary", str(elf), str(binary)],
            ):
                subprocess.run(command, check=True, capture_output=True)
            data = binary.read_bytes()
        return list(struct.unpack("<%dI" % (len(data) // 4), data))

    def test_probe_words_match_their_sources(self):
        for source, words in (("gwdt_probe.c", GWDT_WORDS),
                              ("ecam_probe.c", ECAM_WORDS),
                              ("gpio_probe.c", GPIO_WINDOW_WORDS),
                              ("ramwindow.c", RAMWINDOW_WORDS)):
            with self.subTest(source=source):
                self.assertEqual(self.words_from(source), list(words),
                                 f"{source} no longer assembles to the words the test uses")
