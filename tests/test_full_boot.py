"""Synthetic package, reserved-memory and functional NOR tests; no vendor bytes."""

import ctypes
import hashlib
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

from tools.handoff import boot_arguments
from tools.prepare_mm import unpack_mm

ROOT = Path(__file__).resolve().parents[1]


class MmPackageTests(unittest.TestCase):
    def package(self):
        data = bytearray(0x200 + 0x100)
        struct.pack_into("<8I", data, 0, 0x474b5053, 2, 0x100, 40, 0x200, 0x100, 0, 0)
        struct.pack_into(">II", data, 0x100, 0xd00dfeed, 40)
        data[0x228:0x22c] = b"_FVH"
        return bytes(data)

    def test_erase_latency_defaults_to_the_program_value(self):
        from tools.build_monitor import latency_flags
        self.assertEqual(latency_flags(0), [])
        self.assertEqual(latency_flags(400), ["-DNOR_WRITE_US=400"])
        self.assertEqual(latency_flags(400, 40000),
                         ["-DNOR_WRITE_US=400", "-DNOR_ERASE_US=40000"])
        with self.assertRaisesRegex(ValueError, "erase latency"):
            latency_flags(0, 300000)

    def test_package_is_extracted_unchanged_and_hash_checked(self):
        package = self.package()
        self.assertEqual(unpack_mm(b"synthetic-prefix" + package, hashlib.sha256(package).hexdigest()), package)
        with self.assertRaisesRegex(ValueError, "checksum"):
            unpack_mm(package)

    def test_rejects_ambiguous_truncated_and_overlapping_packages(self):
        package = self.package()
        for data in (package * 2, package[:-1]):
            with self.assertRaisesRegex(ValueError, "valid SPKG"):
                unpack_mm(data)
        broken = bytearray(package)
        struct.pack_into("<I", broken, 16, 0x110)
        with self.assertRaises(ValueError):
            unpack_mm(broken)

    def test_free_memory_does_not_overlap_live_monitor_mm_or_nor(self):
        data = boot_arguments()
        self.assertEqual(len(data), 0x1870)
        self.assertEqual(data[-8:], b"\0" * 8)
        count = struct.unpack_from("<I", data, 0x78)[0]
        regions = [struct.unpack_from("<QQI", data, 0x80 + i * 56 + 32) for i in range(count)]
        self.assertEqual(count, 8)
        reserved = [(0x80000000, 0x80100000), (0x933e0000, 0x94240000),
                    (0xa0000000, 0xa0100000)]
        for base, size, flags in regions:
            if flags == 1:
                for start, end in reserved:
                    self.assertTrue(base >= end or base + size <= start)
        memory = sorted((base, base + size) for base, size, _ in regions if base >= 0x80000000)
        self.assertEqual(memory[0][0], 0x80000000)
        self.assertEqual(memory[-1][1], 0xc0000000)
        self.assertTrue(all(left[1] == right[0] for left, right in zip(memory, memory[1:])))


@unittest.skipUnless(shutil.which("cc"), "C compiler required for the functional NOR service")
class NorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / "build").mkdir(exist_ok=True)
        cls.directory = tempfile.TemporaryDirectory(prefix="nor-test-", dir=ROOT / "build")
        library = Path(cls.directory.name) / "nor.so"
        subprocess.run([shutil.which("cc"), "-shared", "-fPIC", "-Wall", "-Wextra", "-Werror",
                        str(ROOT / "monitor/nor.c"), "-o", str(library)], check=True)
        cls.library = ctypes.CDLL(str(library))
        cls.library.nor_request.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int]
        cls.library.nor_request.restype = ctypes.c_int
        cls.library.nor_set_policy_armed.argtypes = [ctypes.c_int]

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        self.flash = ctypes.create_string_buffer(b"\xff" * 0x2000000, 0x2000000)
        self.library.nor_set_policy_armed(1)

    def request(self, command, address=0x3000000, data=b"", subcommand=0, capacity=8192,
                trusted=1):
        request = bytearray(capacity)
        request[0] = command
        request[5] = subcommand
        struct.pack_into("<I", request, 1, len(data))
        struct.pack_into("<Q", request, 7, address)
        request[32:32 + len(data)] = data
        message = ctypes.create_string_buffer(bytes(request), len(request))
        self.assertEqual(self.library.nor_request(self.flash, len(self.flash), message,
                                                  len(message), trusted), 1)
        return message.raw

    def test_program_read_and_erase_have_real_nor_semantics(self):
        self.assertEqual(self.request(1, data=b"hello")[5], 0)
        reply = self.request(0, data=struct.pack("<I", 5))
        self.assertEqual(struct.unpack_from("<I", reply, 1)[0], 5)
        self.assertEqual(reply[32:37], b"hello")
        self.assertEqual(self.request(2, data=struct.pack("<I", 4096))[5], 0)
        self.assertEqual(self.flash.raw[:4096], b"\xff" * 4096)

    def test_a_program_spanning_pages_reads_back_verbatim(self):
        # The program path commits a page at a time now, so a request larger than one page must
        # still apply every byte — including the ones that straddle a page boundary.
        payload = bytes(range(256)) * 4
        self.assertEqual(self.request(1, data=payload)[5], 0)
        reply = self.request(0, data=struct.pack("<I", len(payload)))
        self.assertEqual(struct.unpack_from("<I", reply, 1)[0], len(payload))
        self.assertEqual(reply[32:32 + len(payload)], payload)
        # A second program that only clears bits inside the same span still lands per page.
        half = bytes(len(payload))
        self.assertEqual(self.request(1, data=half)[5], 0)
        reply = self.request(0, data=struct.pack("<I", len(payload)))
        self.assertEqual(reply[32:32 + len(payload)], half)

    def test_invalid_program_is_atomic_and_cannot_set_erased_bits(self):
        self.request(1, data=b"\x00\xff")
        self.assertEqual(self.request(1, data=b"\xff\x00")[5], 1)
        self.assertEqual(self.flash.raw[:2], b"\x00\xff")

    def test_bounds_alignment_and_unknown_commands_fail_without_writes(self):
        before = self.flash.raw
        for command, address, data in [(1, 0x2ffffff, b"x"), (1, 0x4ffffff, b"xx"),
                                        (1, 0xffffffffffffffff, b"xx"),
                                        (2, 0x3000001, struct.pack("<I", 4096)),
                                        (2, 0x3000000, struct.pack("<I", 1)), (99, 0, b"")]:
            self.assertEqual(self.request(command, address, data)[5], 1)
        self.assertEqual(self.flash.raw, before)

    def test_partition_geometry_and_flush(self):
        reply = self.request(3, subcommand=3)
        self.assertEqual(struct.unpack_from("<I", reply, 1)[0], 33)
        self.assertEqual(reply[32], 1)
        self.assertEqual(struct.unpack_from("<QQ", reply, 0x31), (0x3000000, 0x2000000))
        self.assertEqual(self.request(8)[5], 0)

    def test_armed_latch_denies_an_untrusted_sender_with_the_board_status(self):
        self.request(1, data=b"secret", trusted=1)
        # The board answers a denied request with 0x0A and no payload.
        denied = self.request(0, data=struct.pack("<I", 6), trusted=0)
        self.assertEqual(denied[5], 0x0A)
        self.assertEqual(struct.unpack_from("<I", denied, 1)[0], 0)
        self.assertNotEqual(denied[32:38], b"secret")
        # The trusted sender is unaffected by the latch.
        allowed = self.request(0, data=struct.pack("<I", 6), trusted=1)
        self.assertEqual(allowed[5], 0)
        self.assertEqual(allowed[32:38], b"secret")

    def test_open_policy_is_the_pre_arming_window(self):
        self.library.nor_set_policy_armed(0)
        self.request(1, data=b"window", trusted=1)
        reply = self.request(0, data=struct.pack("<I", 6), trusted=0)
        self.assertEqual(reply[5], 0)
        self.assertEqual(reply[32:38], b"window")

    def test_command_eight_is_trusted_only_and_arms_the_latch(self):
        self.library.nor_set_policy_armed(0)
        self.assertEqual(self.request(8, trusted=0)[5], 0x0A)
        self.assertEqual(self.library.nor_policy_armed(), 0)
        self.assertEqual(self.request(8, trusted=1)[5], 0)
        self.assertEqual(self.library.nor_policy_armed(), 1)
        self.assertEqual(self.request(0, data=struct.pack("<I", 1), trusted=0)[5], 0x0A)

    def test_command_eight_needs_a_zero_payload_to_arm(self):
        self.library.nor_set_policy_armed(0)
        self.assertEqual(self.request(8, data=struct.pack("<I", 1), trusted=1)[5], 1)
        self.assertEqual(self.library.nor_policy_armed(), 0)

    def test_denial_precedes_every_semantic_check(self):
        # The board answers 0x0A whatever the sender asked for, including a
        # message too short to parse and one whose length field is nonsense.
        short = ctypes.create_string_buffer(b"\x00" * 8, 8)
        self.assertEqual(self.library.nor_request(self.flash, len(self.flash), short, 8, 0), 1)
        self.assertEqual(short.raw[5], 0x0A)
        malformed = self.request(1, address=0x2ffffff, data=b"x", trusted=0)
        self.assertEqual(malformed[5], 0x0A)

    def test_truncated_messages_are_rejected(self):
        message = ctypes.create_string_buffer(b"\0" * 79, 79)
        self.assertEqual(self.library.nor_request(self.flash, len(self.flash), message, 79, 1), 0)
