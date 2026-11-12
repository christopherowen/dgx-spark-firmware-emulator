"""The ordering the whole readback question turns on, read out of a real run's serial log.

The firmware sends the arming request (command 8) during boot, and a boot-option payload runs later. In
the emulator both are visible in one log, so the ordering is observable rather than assumed: the log is
kept under `firmware/runs/`, and this test finds a run that has both and asserts which came first.

It is a test of the *record and the ordering claim*, not of a unit: the run is the modelled profile.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "firmware" / "runs"


def interesting_runs():
    if not RUNS.is_dir():
        return []
    found = []
    for directory in sorted(RUNS.iterdir(), reverse=True):
        log = directory / "serial.log"
        if not log.is_file():
            continue
        text = log.read_text(errors="replace")
        if "FLASH cmd=08" in text and "NOR-START" in text:
            found.append(log)
    return found


class LockoutOrderingTests(unittest.TestCase):
    def test_the_arming_request_precedes_the_boot_option_payload(self):
        logs = interesting_runs()
        if not logs:
            self.skipTest("no run has both the arming request and the payload in its log")
        text = logs[0].read_text(errors="replace")
        lines = text.splitlines()
        arm = next(index for index, line in enumerate(lines) if "FLASH cmd=08" in line)
        payload = next(index for index, line in enumerate(lines) if "NOR-START" in line)
        self.assertLess(arm, payload,
                        "the trusted endpoint arms the lockout before a boot option runs, which is why the "
                        "payload's request is refused")

    def test_the_payload_reports_the_service_answering_and_refusing(self):
        logs = interesting_runs()
        if not logs:
            self.skipTest("no qualifying run")
        text = logs[0].read_text(errors="replace")
        self.assertIn("DIRECT_RESP", text.replace("reply=00000000c4000070", "DIRECT_RESP"),
                      "the call reached the service")
        self.assertIn("FLASH DENY", text, "and the service refused it")


if __name__ == "__main__":
    unittest.main()
