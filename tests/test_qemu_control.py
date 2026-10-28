"""Protocol-client checks use synthetic replies without firmware or an emulator."""

import io
import json
import unittest
from unittest.mock import Mock

from tools.qemu_control import GdbRemote, Qmp


class ReplyStream:
    def __init__(self, replies):
        self.replies = io.BytesIO(b"".join(json.dumps(reply).encode() + b"\n" for reply in replies))
        self.commands = []
        self.closed = False

    def readline(self):
        return self.replies.readline()

    def write(self, data):
        self.commands.append(json.loads(data))

    def close(self):
        self.closed = True


class QmpTests(unittest.TestCase):
    def connection(self, replies):
        connection = Mock()
        connection.makefile.return_value = ReplyStream(replies)
        return connection

    def test_events_are_skipped_and_connections_close(self):
        connection = self.connection([
            {"QMP": {}}, {"return": {}}, {"event": "STOP"}, {"return": {"running": False}},
        ])
        with Qmp(connection) as qmp:
            self.assertEqual(qmp.request("query-status"), {"running": False})
        self.assertEqual(connection.makefile.return_value.commands, [
            {"execute": "qmp_capabilities", "arguments": {}},
            {"execute": "query-status", "arguments": {}},
        ])
        self.assertTrue(connection.makefile.return_value.closed)
        connection.close.assert_called_once()

    def test_command_error_and_eof_close_the_connection(self):
        for response in ([{"error": {"class": "GenericError", "desc": "synthetic error"}}], []):
            connection = self.connection([{"QMP": {}}, {"return": {}}, *response])
            with self.subTest(response=response), self.assertRaises(RuntimeError):
                with Qmp(connection) as qmp:
                    qmp.request("stop")
            self.assertTrue(connection.makefile.return_value.closed)
            connection.close.assert_called_once()

    def test_failed_handshake_closes_the_connection(self):
        connection = self.connection([{"unexpected": {}}])
        with self.assertRaisesRegex(RuntimeError, "greeting"):
            Qmp(connection)
        self.assertTrue(connection.makefile.return_value.closed)
        connection.close.assert_called_once()


class GdbTests(unittest.TestCase):
    def test_breakpoint_requires_exact_pc_and_an_executed_instruction(self):
        class Remote(GdbRemote):
            def __init__(self, pcs):
                self.pcs = iter(pcs)

            def request(self, command):
                return "T05" if command in ("c", "s") else "OK"

            def pc(self):
                return next(self.pcs)

        self.assertEqual(Remote([0x1234, 0x1238]).checkpoint(0x1234),
                         {"entry_pc": "0x1234", "pc_after_single_step": "0x1238"})
        for pcs in ([0x9999], [0x1234, 0x1234]):
            with self.subTest(pcs=pcs), self.assertRaises(RuntimeError):
                Remote(pcs).checkpoint(0x1234)
