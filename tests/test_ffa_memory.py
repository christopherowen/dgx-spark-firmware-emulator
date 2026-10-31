"""Exercise the monitor's actual descriptor/lifetime code with synthetic memory."""

import ctypes
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
Words = ctypes.c_ulong * 12
Mailboxes = ctypes.c_ulong * 4


class Share(ctypes.Structure):
    _fields_ = [("descriptor", Words), ("handle", ctypes.c_ulong),
                ("next_handle", ctypes.c_ulong), ("retrieved", ctypes.c_int)]


@unittest.skipUnless(shutil.which("cc"), "C compiler required for FF-A descriptor tests")
class FfaMemoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / "build").mkdir(exist_ok=True)
        cls.directory = tempfile.TemporaryDirectory(prefix="ffa-test-", dir=ROOT / "build")
        library = Path(cls.directory.name) / "ffa.so"
        subprocess.run([shutil.which("cc"), "-shared", "-fPIC", "-Wall", "-Wextra", "-Werror",
                        str(ROOT / "monitor/ffa_memory.c"), "-o", str(library)], check=True)
        cls.lib = ctypes.CDLL(str(library))
        word_pointer = ctypes.POINTER(ctypes.c_ulong)
        cls.lib.ffa_ns_buffer.argtypes = [ctypes.c_ulong, ctypes.c_ulong]
        cls.lib.ffa_memory_share.argtypes = [ctypes.POINTER(Share), word_pointer, word_pointer]
        cls.lib.ffa_memory_retrieve.argtypes = [ctypes.POINTER(Share), word_pointer, word_pointer]
        cls.lib.ffa_memory_relinquish.argtypes = [ctypes.POINTER(Share), word_pointer]
        cls.lib.ffa_memory_reclaim.argtypes = [ctypes.POINTER(Share), ctypes.c_ulong]

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        self.state = Share()
        self.mailboxes = Mailboxes(0x88000000, 0x88001000, 0x94140000, 0x94141000)

    def descriptor(self, address=0x88002000, pages=2, permission=6):
        return Words(0x2f0000, 0, 0xfeed1234, (1 << 32) | 16, 48, 0,
                     (64 << 32) | (permission << 16) | 0x8004, 0,
                     (1 << 32) | pages, 0, address, pages)

    def share(self, descriptor=None):
        return self.lib.ffa_memory_share(ctypes.byref(self.state),
                                         descriptor if descriptor is not None else self.descriptor(),
                                         self.mailboxes)

    def retrieve_request(self, permission=0):
        return Words(0, self.state.handle, 0xfeed1234, (1 << 32) | 16, 48, 0,
                     (permission << 16) | 0x8004, 0)

    def retrieve(self, request=None):
        response = Words()
        error = self.lib.ffa_memory_retrieve(ctypes.byref(self.state),
                                             request if request is not None else self.retrieve_request(),
                                             response)
        return error, response

    def relinquish(self, request=None):
        request = request if request is not None else Words(self.state.handle, 1 << 32, 0x8004)
        return self.lib.ffa_memory_relinquish(ctypes.byref(self.state), request)

    def reclaim(self, handle=None):
        return self.lib.ffa_memory_reclaim(ctypes.byref(self.state),
                                           self.state.handle if handle is None else handle)

    def test_descriptor_is_copied_and_retrieve_marks_ns_without_changing_data_region(self):
        descriptor = self.descriptor()
        self.assertEqual(self.share(descriptor), 0)
        descriptor[10] = 0xdead0000
        error, reply = self.retrieve()
        self.assertEqual(error, 0)
        self.assertEqual(reply[0], (8 << 32) | (0x6f << 16))
        self.assertEqual(reply[1], self.state.handle)
        self.assertEqual(reply[2], 0xfeed1234)
        self.assertEqual(reply[6], (64 << 32) | (6 << 16) | 0x8004)
        self.assertEqual((reply[8], reply[10], reply[11]), ((1 << 32) | 2, 0x88002000, 2))

    def test_reclaim_waits_for_relinquish_and_stale_handles_do_not_alias(self):
        self.assertEqual(self.share(), 0)
        old_handle = self.state.handle
        self.assertNotEqual(old_handle, 1)
        self.assertEqual(self.retrieve()[0], 0)
        self.assertEqual(self.reclaim(), -6)
        self.assertEqual(self.retrieve()[0], -6)
        self.assertEqual(self.relinquish(), 0)
        self.assertEqual(self.relinquish(), -6)
        self.assertEqual(self.reclaim(), 0)
        self.assertEqual(self.reclaim(old_handle), -2)
        self.assertEqual(self.share(), 0)
        self.assertGreater(self.state.handle, old_handle)
        self.assertEqual(self.reclaim(old_handle), -2)
        self.assertEqual(self.reclaim(), 0)

    def test_unretrieved_transaction_can_be_reclaimed_and_capacity_does_not_overwrite(self):
        self.assertEqual(self.share(), 0)
        saved = bytes(self.state)
        self.assertEqual(self.share(self.descriptor(0x88100000)), -3)
        self.assertEqual(bytes(self.state), saved)
        self.assertEqual(self.reclaim(), 0)
        self.state.next_handle = 0x7fffffffffffffff
        self.assertEqual(self.share(), -3)
        self.assertEqual(self.state.handle, 0)

    def test_rejects_malformed_descriptor_without_allocating_a_handle(self):
        corruptions = [(0, 0x6f0000), (0, (1 << 32) | 0x2f0000), (1, 1),
                       (3, (2 << 32) | 16), (3, (1 << 32) | 32), (4, 64), (5, 1),
                       (6, (80 << 32) | (6 << 16) | 0x8004),
                       (6, (64 << 32) | (6 << 16) | 0x8002),
                       (6, (64 << 32) | (10 << 16) | 0x8004), (7, 1),
                       (8, (1 << 32) | 3), (9, 1), (10, 0x88002001),
                       (11, 0), (11, (1 << 32) | 2)]
        for index, value in corruptions:
            with self.subTest(index=index, value=value):
                d = self.descriptor()
                d[index] = value
                self.assertEqual(self.share(d), -2)
                self.assertEqual(self.state.handle, 0)
        self.assertEqual(self.share(self.descriptor(pages=33)), -2)

    def test_sender_spoofing_secure_memory_nor_mmio_and_wrapping_ranges_are_denied(self):
        d = self.descriptor()
        d[0] |= 0x8004
        self.assertEqual(self.share(d), -6)
        for address, pages in [(0, 1), (0x80020000, 1), (0x93400000, 1),
                               (0x93a40000, 1), (0x9423f000, 2),
                               (0x933ff000, 2), (0xa0000000, 1), (0x9ffff000, 2),
                               (0xbffff000, 2), (0xfffffffffffff000, 2)]:
            with self.subTest(address=hex(address), pages=pages):
                self.assertEqual(self.share(self.descriptor(address, pages)), -6)
                self.assertEqual(self.state.handle, 0)

    def test_fixed_manifest_ns_buffer_and_ram_boundary_are_allowed(self):
        for address, pages in [(0x933e0000, 32), (0x933ff000, 1), (0x94240000, 1),
                               (0xa0100000, 1), (0xbffff000, 1)]:
            with self.subTest(address=hex(address)):
                self.assertEqual(self.share(self.descriptor(address, pages)), 0)
                self.assertEqual(self.reclaim(), 0)

    def test_mailbox_pages_cannot_be_shared_even_when_transaction_contains_them(self):
        for address, pages in [(0x88000000, 1), (0x88001000, 1), (0x87fff000, 3)]:
            self.assertEqual(self.share(self.descriptor(address, pages)), -6)
        self.mailboxes[2] = 0x88200000
        self.mailboxes[3] = 0x88201000
        for address in self.mailboxes[2:]:
            self.assertEqual(self.share(self.descriptor(address, 1)), -6)

    def test_retrieve_validates_handle_tag_recipient_flags_and_permissions(self):
        self.assertEqual(self.share(), 0)
        for index, value in [(0, 0x6f0000), (0, 16 << 32), (1, 99),
                             (2, 0), (3, 16), (4, 64), (5, 1), (6, 0x8002),
                             (6, (64 << 32) | 0x8004), (6, (10 << 16) | 0x8004), (7, 1)]:
            with self.subTest(index=index, value=value):
                request = self.retrieve_request()
                request[index] = value
                self.assertEqual(self.retrieve(request)[0], -2)
                self.assertEqual(self.state.retrieved, 0)
        request = self.retrieve_request()
        request[0] = 1
        self.assertEqual(self.retrieve(request)[0], -6)

    def test_read_only_cannot_be_escalated_and_rw_can_be_retrieved_read_only(self):
        self.assertEqual(self.share(self.descriptor(permission=5)), 0)
        self.assertEqual(self.retrieve(self.retrieve_request(6))[0], -6)
        self.assertEqual(self.retrieve(self.retrieve_request(5))[0], 0)
        self.assertEqual(self.relinquish(), 0)
        self.assertEqual(self.reclaim(), 0)
        self.assertEqual(self.share(), 0)
        error, response = self.retrieve(self.retrieve_request(1))
        self.assertEqual(error, 0)
        self.assertEqual(response[6] >> 16 & 255, 5)
        self.assertEqual(self.relinquish(), 0)
        error, response = self.retrieve(self.retrieve_request(4))
        self.assertEqual(error, 0)
        self.assertEqual(response[6] >> 16 & 255, 6)

    def test_relinquish_requires_the_borrower_and_correct_handle_without_clear_flags(self):
        self.assertEqual(self.share(), 0)
        self.assertEqual(self.retrieve()[0], 0)
        for request in [Words(99, 1 << 32, 0x8004),
                        Words(self.state.handle, (1 << 32) | 1, 0x8004),
                        Words(self.state.handle, 2 << 32, 0x8004),
                        Words(self.state.handle, 1 << 32, 0)]:
            self.assertEqual(self.relinquish(request), -2)
            self.assertEqual(self.state.retrieved, 1)
        self.assertEqual(self.relinquish(), 0)
