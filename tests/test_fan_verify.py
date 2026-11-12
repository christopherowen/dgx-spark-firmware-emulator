"""Execute the verifier's read-only pass with a recording sysfs/sudo transport.

The fixture distinguishes the driver's automatic-mode refusal from its allow_pin
refusal. No module, guest, hardware access or compiler is used.
"""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "fan_verify.sh"
TRANSPORT = r'''
set -u
MODULE=fixture.ko
kv() { printf '%s=%s\n' "$1" "$2"; }
say() { printf '%s\n' "$*"; }
sleep() { :; }
hwmon_dir() { [ "$PRESENT" = 1 ] && printf '%s\n' "$HWMON"; }
cooling_dir() { :; }
sudo() {
    [ "$1" = -n ] && shift
    case "$1" in
    rmmod|insmod) return 0 ;;
    tee)
        path=$2
        IFS= read -r value
        case "${path##*/}" in
        pwm1_enable)
            if [ "$REJECT_MANUAL" = 1 ]; then
                printf '%s\n' mode-refused >> "$TRACE"
                return 1
            fi
            printf '%s\n' "$value" > "$path"
            printf 'mode=%s\n' "$value" >> "$TRACE"
            ;;
        pwm1)
            if [ "$(cat "$HWMON/pwm1_enable")" != 1 ]; then
                printf '%s\n' automatic-refused >> "$TRACE"
                return 1
            fi
            if [ "$BYPASS_ALLOW_PIN" != 1 ]; then
                printf '%s\n' allow-pin-refused >> "$TRACE"
                return 1
            fi
            printf 'would-pin=%s\n' "$value" >> "$TRACE"
            ;;
        *) return 99 ;;
        esac
        ;;
    *) return 99 ;;
    esac
}
'''


class FanVerifierTests(unittest.TestCase):
    def run_readonly(self, present=True, bypass_allow_pin=False, reject_manual=False):
        # Execute the production pass itself, not a copied version of its guard.
        text = SCRIPT.read_text()
        readonly = text.split("# 2. Read-only pass:", 1)[1].split("# 3. Optional write pass.", 1)[0]
        readonly = "# 2. Read-only pass:" + readonly
        with tempfile.TemporaryDirectory(prefix=".fan-verify-test-", dir=ROOT) as temporary:
            directory = Path(temporary)
            for name, value in {"pwm1_enable": "2", "pwm1": "232",
                                "fan1_input": "2700", "fan2_input": "4050"}.items():
                (directory / name).write_text(value + "\n")
            trace = directory / "trace"
            trace.touch()
            result = subprocess.run(
                ["sh", "-c", TRANSPORT + readonly], capture_output=True, text=True,
                env={**os.environ, "HWMON": str(directory), "TRACE": str(trace),
                     "PRESENT": str(int(present)), "BYPASS_ALLOW_PIN": str(int(bypass_allow_pin)),
                     "REJECT_MANUAL": str(int(reject_manual))})
            return result, trace.read_text().splitlines()

    def test_manual_mode_reaches_the_allow_pin_guard_without_an_ec_write(self):
        result, trace = self.run_readonly()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("write_refused_without_allow_pin=yes", result.stdout)
        self.assertEqual(trace, ["mode=1", "allow-pin-refused"])

    def test_a_bypassed_allow_pin_guard_is_not_hidden_by_automatic_mode(self):
        result, trace = self.run_readonly(bypass_allow_pin=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("write_refused_without_allow_pin=NO", result.stdout)
        self.assertEqual(trace, ["mode=1", "would-pin=200"])

    def test_only_an_absent_hwmon_device_reports_not_applicable(self):
        result, trace = self.run_readonly(present=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("write_refused_without_allow_pin=n/a", result.stdout)
        self.assertEqual(trace, [])

    def test_manual_mode_failure_is_an_error_not_a_verified_guard(self):
        result, trace = self.run_readonly(reject_manual=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("write_refused_without_allow_pin=yes", result.stdout)
        self.assertNotIn("write_refused_without_allow_pin=n/a", result.stdout)
        self.assertEqual(trace, ["mode-refused"])


if __name__ == "__main__":
    unittest.main()
