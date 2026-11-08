"""Integration gate for the store findings.

These boot the emulator, so they are opt-in: set SPARK_INTEGRATION=1 to run them.
Everything else in the suite stays fast. All three ran green on 2026-11-06 in
144 seconds, on a quiet process table - the state file is flock-serialised, so a
second boot of the same store reports EAGAIN instead of a result.

The three cases are the claims the work rests on:
  * the store an accepted insert produces does not reach setup, and prints only 61;
  * the same store with the index tombstoned does reach setup;
  * a store that never had a DMI insert holds no DMI variables.
"""

import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "firmware" / "state"
ENABLED = os.environ.get("SPARK_INTEGRATION") == "1"


def boot(state: Path, timeout: int = 90, extra=()) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "tools/boot_spark.py", "--smoke-test", f"--timeout={timeout}",
         "--state-file", str(state), *extra],
        cwd=ROOT, capture_output=True, text=True,
        env={**os.environ, "PATH": f"{ROOT / 'build' / 'host-tools' / 'bin'}:{os.environ['PATH']}"})


def run_directory(result: subprocess.CompletedProcess) -> Path:
    """The run directory this boot created, taken from the harness's own output.

    Reading "newest run for this state file" would happily return an older boot
    of the same fixture, so the path is taken from the run that just happened.
    """
    for line in (result.stdout + result.stderr).splitlines():
        if "Local logs:" in line:
            return Path(line.split("Local logs:", 1)[1].strip())
    raise AssertionError(f"no run directory in the harness output:\n{result.stdout}\n{result.stderr}")


def record_for(result: subprocess.CompletedProcess) -> dict:
    import json
    return json.loads((run_directory(result) / "run.json").read_text())


def checkpoints(result: subprocess.CompletedProcess) -> int:
    log = run_directory(result) / "serial.log"
    return sum(1 for line in log.read_text(errors="ignore").splitlines()
               if line.startswith("Checkpoint"))


@unittest.skipUnless(ENABLED, "set SPARK_INTEGRATION=1 to boot the emulator")
class StoreBehaviour(unittest.TestCase):
    def require(self, name: str) -> Path:
        state = STATE / name
        if not state.is_file():
            self.skipTest(f"fixture {name} is not present")
        return state

    def disposable(self, name: str) -> Path:
        """Copy a fixture so the boot mutates the copy, never the fixture."""
        import uuid
        source = self.require(name)
        target = STATE / f"tmp-{uuid.uuid4().hex[:12]}.bin"
        target.write_bytes(source.read_bytes())
        self.addCleanup(target.unlink, missing_ok=True)
        return target

    def test_accepted_insert_does_not_reach_setup(self):
        result = boot(self.disposable("p1-arm1.bin"))
        self.assertNotEqual(result.returncode, 0, result.stdout)
        # This run's own record is the authority, not the console wording: a
        # hang shows up as setup never becoming visible in the run just made.
        record = record_for(result)
        self.assertIn("error", record)          # the run failed, and says why
        self.assertNotIn("result", record)      # only a completed run records a result
        self.assertFalse(record.get("setup_visible", False))
        self.assertGreaterEqual(checkpoints(result), 2)

    def test_tombstoned_index_reaches_setup(self):
        result = boot(self.disposable("p3-index.bin"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(record_for(result).get("setup_visible"))

    def test_pristine_store_holds_no_dmi_variables(self):
        sys.path.insert(0, str(ROOT))
        from tools.nor_edit import variables

        state = self.require("p1-arm0-consume.bin")
        names = [v.name for v in variables(state.read_bytes())]
        self.assertEqual([n for n in names if n.startswith("Dmi")], [])

    def test_a_clean_store_stays_structurally_clean(self):
        # The model must not introduce a structural finding of its own: a clean input, booted,
        # still passes the store parser's poison check.
        sys.path.insert(0, str(ROOT))
        from tools.nor_edit import poison_findings

        state = self.disposable("allff.bin")
        self.assertEqual(poison_findings(state.read_bytes()), [], "fixture is not clean")
        result = boot(state)
        # The harness only replaces the input on its success path, so a failed boot would leave the
        # pristine input in place and the check below would pass on bytes no boot ever touched.
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        record = record_for(result)
        self.assertTrue(record.get("setup_visible"), record.get("error"))
        self.assertEqual(poison_findings(state.read_bytes()), [],
                         "the boot left a structural finding in the store")

    def test_a_code_volume_edit_is_what_the_guest_prints(self):
        # The point of the whole-device layout: a byte written into the code volume is what the guest
        # executes, not a host file loaded beside it. The build string the console prints is the
        # observable, and it is patched inside the image rather than passed as a file.
        sys.path.insert(0, str(ROOT))
        from tools.board_image import BoardImage, split

        image = self.disposable("board-2158.bin")
        parts = split(image.read_bytes())
        token = b"0ACUM028"
        if token not in parts.code:
            self.skipTest("this release does not carry the build token")
        patched = BoardImage(store=parts.store, code=parts.code.replace(token, b"0ACUM999"))
        image.write_bytes(patched.compose())
        result = boot(image, timeout=150,
                      extra=["--board", "--firmware-version", "2.158.15",
                             "--flash-image", str(image)])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        console = (run_directory(result) / "serial.log").read_text(errors="ignore")
        self.assertIn("0ACUM999", console, "the guest did not run the edited code volume")
        self.assertNotIn("0ACUM028", console, "the guest ran the unedited image")

    def test_a_committed_page_survives_a_watchdog_reset(self):
        # The loader's window is a ROM blob and QEMU restores it on every reset, so the monitor
        # serves flash from a second window no loader owns. Without that window a reset would undo
        # everything the previous cycle programmed and a reboot cycle would re-write the same bytes
        # for ever; with it, the writes accumulate. One cycle on this store writes nothing, so the
        # comparison is between one cycle and two.
        one_state, two_state = (self.disposable("board-fatal-2158.bin"),
                                self.disposable("board-fatal-2158.bin"))
        # One cycle writes nothing on this store, so the comparison is one cycle against two; the
        # second needs room for both cycles, and a poisoned cycle is about 70 s on this host.
        one_result = boot(one_state, timeout=120,
                          extra=["--board", "--firmware-version", "2.158.15",
                                 "--flash-image", str(one_state), "--stop-after-boots", "0"])
        two_result = boot(two_state, timeout=240,
                          extra=["--board", "--firmware-version", "2.158.15",
                                 "--flash-image", str(two_state), "--stop-after-boots", "1"])
        two = record_for(two_result)
        self.assertGreaterEqual(two.get("boots_observed") or 0, 2, two.get("checkpoints"))
        # The reset in that run must be the watchdog's, or this proves nothing about a part that
        # keeps its page across the reset the board actually takes.
        self.assertGreaterEqual(two.get("watchdog_resets") or 0, 1, two.get("reset_note"))
        after_two = (run_directory(two_result) / "nor-after.bin").read_bytes()
        after_one = (run_directory(one_result) / "nor-after.bin").read_bytes()
        self.assertNotEqual(after_one, after_two,
                            "the second cycle wrote nothing the first had not already written, "
                            "which is what a reset that restores the loader's image looks like")

    def test_a_boot_count_stop_reports_that_it_never_reached_setup(self):
        # --stop-after-boots measures the failure cycle, not a boot: it must not pass the snapshot
        # checks and must say what it did.
        state = self.disposable("p1-arm1.bin")
        result = boot(state, timeout=240, extra=["--smoke-test", "--stop-after-boots", "1"])
        self.assertNotEqual(result.returncode, 0, result.stdout)
        record = record_for(result)
        self.assertEqual(record.get("boots_observed"), 2, record.get("checkpoints"))
        self.assertIn("without reaching setup", record.get("error") or "")

    def test_flash_trace_records_the_requests_it_answered(self):
        # The diagnostic monitor prints one line per request and the harness counts them; both
        # halves have to work for the run to carry the measurement.
        state = self.disposable("p1-arm1.bin")
        result = boot(state, extra=["--flash-trace"])
        record = record_for(result)
        self.assertGreater(record.get("flash_requests") or 0, 0,
                           f"no request count in the run record:\n{result.stdout}")
        log = (run_directory(result) / "serial.log").read_text(errors="ignore")
        self.assertIn("FLASH cmd=", log)
        # The record has to state the knobs that decided what the guest saw, or the run cannot be
        # reproduced from itself: a store-only boot is partition-only with no code slice.
        self.assertTrue(record.get("flash_trace"), "record does not say the trace build was used")
        self.assertEqual(record.get("nor_offset"), 0x3000000, "record does not say the flash offset")
        self.assertIsNone(record.get("code_offset"), "store-only boot recorded a code offset")
        self.assertEqual(record.get("timeout"), 90, "record does not say the window")
        self.assertTrue(record.get("qemu_sha256"), "record does not identify the machine build")


if __name__ == "__main__":
    unittest.main()
