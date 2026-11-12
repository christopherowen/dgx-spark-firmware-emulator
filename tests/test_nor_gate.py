"""The gate in the modelled flash service: who may ask, and when.

`monitor/nor.c` implements a policy that is evaluated before any semantics: an untrusted sender is
answered `0x0A` when the latch is armed, and command 8 is never honoured for an untrusted sender at all.
That is the whole reason a caller which runs after `ReadyToBoot` cannot read the part, so the policy is
worth a test of its own — a model whose gate is untested can refuse for the wrong reason and nobody
would know.

(The payload that would have exercised this on hardware is local-only by owner rule and is not in this
repository. This file tests the model and names no payload.)
"""

import ctypes
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SOURCE = ROOT / "monitor" / "nor.c"

# The model's defaults (monitor/nor.h): a 32 MiB window mapped at 48 MiB. The request path refuses a
# flash buffer of any other size before it looks at the message, so the test must use the same geometry
# the emulator boots with rather than its own idea of it.
NOR_OFFSET = 0x3000000
NOR_SIZE = 0x2000000
DENY = 0x0A
OK = 0x00
FIRST_DATA_ADDRESS = NOR_OFFSET + 0x1000


def build(directory: Path) -> ctypes.CDLL:
    compiler = shutil.which("cc")
    if not compiler:
        raise unittest.SkipTest("a C compiler is required")
    library = directory / "nor.so"
    subprocess.run([compiler, "-shared", "-fPIC", "-O2", "-Wall",
                    "-I", str(ROOT / "monitor"), str(SOURCE), "-o", str(library)],
                   check=True, capture_output=True)
    model = ctypes.CDLL(str(library))
    model.nor_request.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p,
                                  ctypes.c_ulong, ctypes.c_int]
    model.nor_request.restype = ctypes.c_int
    model.nor_set_policy_armed.argtypes = [ctypes.c_int]
    model.nor_policy_armed.restype = ctypes.c_int
    return model


def status(message) -> int:
    return message[5][0]


class GateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.model = build(Path(cls.directory.name))
        cls.flash = ctypes.create_string_buffer(NOR_SIZE)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        self.model.nor_set_policy_armed(1)

    def message(self, command, request=0, address=FIRST_DATA_ADDRESS, count=0, fill=0,
                subcommand=0):
        buffer = ctypes.create_string_buffer(128)
        for i in range(128):
            buffer[i] = bytes([fill])
        buffer[0] = bytes([command])
        buffer[5] = bytes([subcommand])
        buffer[1:5] = request.to_bytes(4, "little")
        buffer[7:15] = address.to_bytes(8, "little")
        buffer[32:36] = count.to_bytes(4, "little")
        return buffer

    def call(self, message, trusted):
        return self.model.nor_request(self.flash, NOR_SIZE, message, len(message), trusted)

    def test_an_armed_latch_refuses_an_untrusted_read(self):
        message = self.message(0, request=4, count=16)
        self.assertEqual(self.call(message, trusted=0), 1, "a refusal is an answer, not a drop")
        self.assertEqual(status(message), DENY)

    def test_an_armed_latch_still_answers_the_trusted_sender(self):
        message = self.message(0, request=4, count=16)
        self.call(message, trusted=1)
        self.assertEqual(status(message), OK)
        self.assertEqual(self.model.nor_policy_armed(), 1)

    def test_a_clear_latch_dispatches(self):
        self.model.nor_set_policy_armed(0)
        message = self.message(3, request=0, subcommand=3)
        self.call(message, trusted=0)
        self.assertEqual(status(message), OK, "with the latch clear an untrusted caller is served")

    def test_command_eight_is_trusted_only_even_with_a_clear_latch(self):
        self.model.nor_set_policy_armed(0)
        message = self.message(8, request=0, count=0)
        self.call(message, trusted=0)
        self.assertEqual(status(message), DENY, "the arm command never answers an untrusted sender")
        self.assertEqual(self.model.nor_policy_armed(), 0, "and it must not arm the latch")

    def test_the_trusted_sender_arms_the_latch_and_it_stays_armed(self):
        self.model.nor_set_policy_armed(0)
        message = self.message(8, request=0, count=0)
        self.call(message, trusted=1)
        self.assertEqual(self.model.nor_policy_armed(), 1)
        after = self.message(0, request=4, count=16)
        self.call(after, trusted=0)
        self.assertEqual(status(after), DENY, "the latch is never cleared")

    def test_a_refusal_keeps_the_command_byte_and_zeroes_the_payload(self):
        message = self.message(0, request=4, count=16, fill=0xEE)
        self.call(message, trusted=0)
        self.assertEqual(message[0], b"\x00",
                         "the command byte survives so the caller can attribute the refusal")
        self.assertEqual(status(message), DENY)
        self.assertEqual(bytes(message[1:5]), b"\x00\x00\x00\x00",
                         "the payload is cleared, not echoed")


if __name__ == "__main__":
    unittest.main()
