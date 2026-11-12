#!/usr/bin/env python3
"""Replay a recorded run's QEMU command with a QMP socket nobody else holds.

The harness keeps its own QMP client, so an external probe is refused (ConnectionResetError). This
takes the argv the harness recorded in a run's run.json, swaps the serial to a file and the QMP socket
to a path we own, launches it detached, waits for the kernel to reach its last printed line, then reads
runstate, registers and the code at PC.

Usage: replay_and_probe.py <run-dir> [--halt-seconds 300] [--qmp PATH] [--timeout 1800]
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shlex
import socket
import subprocess
import sys
import time


def rewrite(command: list[str], serial: pathlib.Path, qmp: pathlib.Path) -> list[str]:
    out = []
    index = 0
    while index < len(command):
        item = command[index]
        if item == "-serial" and index + 1 < len(command):
            out += ["-serial", f"file:{serial}"]
            index += 2
            continue
        if item == "-qmp" and index + 1 < len(command):
            out += ["-qmp", f"unix:{qmp},server=on,wait=off"]
            index += 2
            continue
        out.append(item)
        index += 1
    return out


def qmp(sock: socket.socket, payload: dict, patience: float = 300.0) -> dict:
    sock.sendall((json.dumps(payload) + "\n").encode())
    sock.settimeout(patience)
    data = b""
    while b"\n" not in data:
        try:
            chunk = sock.recv(65536)
        except socket.timeout:
            return {"error": {"desc": f"no reply in {patience:.0f}s"}}
        if not chunk:
            return {"error": {"desc": "connection closed"}}
        data += chunk
    return json.loads(data.decode().splitlines()[0])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run", type=pathlib.Path)
    parser.add_argument("--halt-seconds", type=float, default=300.0)
    parser.add_argument("--qmp", type=pathlib.Path, default=pathlib.Path("firmware/state/probe.sock"))
    parser.add_argument("--timeout", type=float, default=1800.0)
    parser.add_argument("--keep-alive", action="store_true",
                        help="leave the guest running after probing, for follow-up questions")
    parser.add_argument("--dump", action="append", metavar="HMP",
                        help="extra human-monitor command (repeatable), e.g. 'x/8wx $pc'")
    args = parser.parse_args()

    record = json.loads((args.run / "run.json").read_text())
    serial = args.run / "replay-serial.log"
    serial.write_bytes(b"")
    args.qmp.unlink(missing_ok=True)
    command = rewrite(record["command"], serial.resolve(), args.qmp.resolve())
    print("launching:", " ".join(shlex.quote(part) for part in command[:6]), "...")
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               start_new_session=True)

    started = time.monotonic()
    marker = b"scmi_core"
    while time.monotonic() - started < args.timeout:
        if process.poll() is not None:
            print("qemu exited early:", process.returncode)
            return 1
        if marker in serial.read_bytes():
            print(f"kernel reached scmi_core after {time.monotonic() - started:.0f}s; "
                  f"waiting {args.halt_seconds:.0f}s for the halt")
            time.sleep(args.halt_seconds)
            break
        time.sleep(5)
    else:
        print("never reached scmi_core")
        process.kill()
        return 1

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        for attempt in range(20):
            try:
                connection.connect(str(args.qmp))
                break
            except OSError:
                time.sleep(1)
        else:
            print("cannot connect to the socket")
            process.kill()
            return 1
        greeting = connection.recv(65536)
        print("greeting:", greeting[:60])
        qmp(connection, {"execute": "qmp_capabilities"})
        for line in ("query-status",):
            print(f"=== {line}", qmp(connection, {"execute": line}))
        for command_line in ("info registers", "x/8wx $pc", "info cpus") + tuple(args.dump or ()):
            reply = qmp(connection, {"execute": "human-monitor-command",
                                     "arguments": {"command-line": command_line}})
            print(f"=== {command_line}\n{reply.get('return') or reply}")
    if args.keep_alive:
        print(f"leaving qemu alive (pid {process.pid}); socket {args.qmp}")
    else:
        process.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
