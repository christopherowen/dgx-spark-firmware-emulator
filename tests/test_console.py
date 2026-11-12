"""Assert the bytes delivered to serial stdin, not successful QMP keyboard calls."""

import ast
import io
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.console import type_line  # noqa: E402


class Recorder(io.BytesIO):
    def __init__(self):
        super().__init__()
        self.writes = []
        self.flushes = 0

    def write(self, data):
        self.writes.append(data)
        return super().write(data)

    def flush(self):
        self.flushes += 1
        return super().flush()


class TypeLineTests(unittest.TestCase):
    def test_a_representative_shell_command_reaches_serial_unchanged(self):
        line = "cat /sys/kernel/debug/devices_deferred; ls -l /sys/bus/pci/devices/000f:00:02.0/driver"
        stream = Recorder()
        type_line(stream, line)
        self.assertEqual(stream.getvalue(), (line + "\n").encode())

    def test_uppercase_and_punctuation_are_bytes_not_key_chords(self):
        stream = Recorder()
        type_line(stream, "A:1 > /srv/out")
        self.assertEqual(stream.getvalue(), b"A:1 > /srv/out\n")

    def test_utf8_is_delivered_without_a_keyboard_layout_map(self):
        stream = Recorder()
        self.assertEqual(type_line(stream, "\u00e9"), 3)
        self.assertEqual(stream.getvalue(), b"\xc3\xa9\n")

    def test_the_count_includes_the_serial_newline(self):
        stream = Recorder()
        self.assertEqual(type_line(stream, "abc"), 4)
        self.assertEqual(len(stream.getvalue()), 4)

    def test_each_serial_byte_is_flushed_and_paced(self):
        stream = Recorder()
        sleeps = []
        type_line(stream, "a:", delay=0.03, sleep=sleeps.append)
        self.assertEqual(stream.getvalue(), b"a:\n")
        self.assertEqual(stream.writes, [b"a", b":", b"\n"])
        self.assertEqual(stream.flushes, 3)
        self.assertEqual(sleeps, [0.03, 0.03, 0.03])

    def test_an_empty_line_sends_enter_and_leaves_the_stream_open(self):
        stream = Recorder()
        type_line(stream, "")
        self.assertEqual(stream.getvalue(), b"\n")
        self.assertFalse(stream.closed)

    def test_the_boot_runner_delivers_script_bytes_to_its_child_stdin(self):
        # Execute the actual scheduled-script branch without starting QEMU.
        tree = ast.parse((ROOT / "tools/boot_spark.py").read_text())
        branch = next(node for node in ast.walk(tree) if isinstance(node, ast.If)
                      and ast.unparse(node.test) == "script_due is not None and now >= script_due")
        stream = Recorder()
        record = {}
        namespace = {"script_due": 0, "now": 1, "script": ["echo ready", "id"],
                     "console_delay": 0, "process": SimpleNamespace(stdin=stream),
                     "qmp": object(), "type_line": type_line, "record": record,
                     "time": SimpleNamespace(sleep=lambda seconds: None)}
        exec(compile(ast.Module(body=[branch], type_ignores=[]), "boot-console-script", "exec"), namespace)
        self.assertEqual(stream.getvalue(), b"echo ready\nid\n")
        self.assertEqual(record["console_script"]["transport"], "serial-stdin")
        self.assertEqual(record["console_script"]["lines"], 2)
        self.assertIsNone(namespace["script_due"])


if __name__ == "__main__":
    unittest.main()
