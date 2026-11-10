"""Execute both probe builds: refused commands must never reach the SMC transport.

Only the privileged transport is substituted. The actual guards and frame encoders
in fan_probe.c run on the host, including setters the READ_ONLY packet does not call.
"""

import unittest
from pathlib import Path

from host_probe import host_probe

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tests" / "fixtures" / "fan_probe.c"
READS = (0x06000704, 0x06000706)
WRITES = tuple(range(0x06000790, 0x06000794))


class AllowlistTests(unittest.TestCase):
    def assert_request(self, invoke, operation, command, value=0):
        result = invoke(operation, hex(command), value)
        opcode = 0x0c if operation == "read" else 0x09
        payload = 2 if operation == "read" else value
        self.assertEqual(result["accepted"], 1)
        self.assertEqual(result["calls"], 1)
        self.assertEqual(result["frame"], [0xc400008d, 0x8003, 0x20418532a0634a88,
                                           0x46a5a008c0eeaa83,
                                           opcode | (command << 8) | (payload << 40)])
        if operation == "read":
            self.assertEqual(result["rpm"], 1978)

    def assert_refused(self, invoke, operation, command):
        result = invoke(operation, hex(command), 255)
        self.assertEqual(result["accepted"], 0)
        self.assertEqual(result["calls"], 0, "a refused request must not leave the probe")

    def test_the_source_states_the_two_sets(self):
        """Both builds emit the two read frames and refuse undocumented command ids."""
        for defines in ((), ("READ_ONLY",)):
            with self.subTest(defines=defines), host_probe(SOURCE, "fan_probe_harness.c", defines) as invoke:
                for command in READS:
                    self.assert_request(invoke, "read", command)
                # Include cross-operation ids, adjacent ids, and a non-32-bit id.
                for command in (0, 0x06000703, 0x06000705, 0x06000707, *WRITES, 0x106000704):
                    self.assert_refused(invoke, "read", command)
                for command in (0, *READS, 0x0600078f, 0x06000794, 0x106000790):
                    self.assert_refused(invoke, "write", command)

    def test_the_read_only_build_carries_no_write_opcode(self):
        """Even a direct call to a compiled-in setter cannot emit a write in READ_ONLY."""
        with host_probe(SOURCE, "fan_probe_harness.c", ("READ_ONLY",)) as invoke:
            for command in WRITES:
                self.assert_refused(invoke, "write", command)

    def test_the_full_build_still_offers_the_fan_sequence(self):
        """The full build emits every documented setter with the byte payload intact."""
        with host_probe(SOURCE, "fan_probe_harness.c") as invoke:
            for command in WRITES:
                for value in (0, 255):
                    self.assert_request(invoke, "write", command, value)


if __name__ == "__main__":
    unittest.main()
