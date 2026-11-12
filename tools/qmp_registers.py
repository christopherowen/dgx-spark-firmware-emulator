#!/usr/bin/env python3
"""Ask a running guest where it is, through QMP.

A guest that stops printing is either waiting or looping, and the register file settles which. This
connects to the socket `boot_spark.py --qmp-alive PATH` leaves behind, asks the human monitor for
`info registers`, and prints the fields that matter for a stall: PC, LR, SPSR and SP.

QMP is used rather than the trace plugin because the plugin costs more than it reveals on this machine:
its per-sample output slows the boot enough to miss the window it is meant to measure.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import socket
import sys

INTERESTING = ("PC=", "LR=", "SP=", "SPSR", "PSTATE", "ELR", "FAR", "ESR")


def talk(sock: socket.socket, payload: dict, patience: float = 180.0) -> dict:
    """One QMP request, one reply. The patience matters: while the guest loops in a translation
    block it holds the BQL, and QMP commands are only serviced when it lets go -- 69 seconds is
    measured, not guessed, so a short timeout reports a hang that is not one."""
    sock.sendall((json.dumps(payload) + "\n").encode())
    sock.settimeout(patience)
    data = b""
    while b"\n" not in data:
        try:
            chunk = sock.recv(65536)
        except socket.timeout:
            raise SystemExit(f"error: no reply within {patience:.0f}s; the guest is not yielding")
        if not chunk:
            break
        data += chunk
    return json.loads(data.decode().splitlines()[0])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--socket", type=pathlib.Path, required=True)
    parser.add_argument("--wait", type=float, default=10.0,
                        help="seconds to keep trying the connection")
    parser.add_argument("--all", action="store_true", help="print the whole register dump")
    parser.add_argument("--command", action="append", metavar="HMP",
                        help="run this human-monitor command instead of 'info registers' (repeatable)")
    args = parser.parse_args()

    import time
    deadline = time.monotonic() + args.wait
    connection = None
    while time.monotonic() < deadline:
        try:
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connection.connect(str(args.socket))
            break
        except OSError:
            connection = None
            time.sleep(0.5)
    if connection is None:
        raise SystemExit(f"error: cannot connect to {args.socket}")

    with connection:
        greeting = connection.recv(65536)  # the server sends its capabilities unprompted
        if not greeting:
            raise SystemExit("error: the socket closed before the greeting")
        talk(connection, {"execute": "qmp_capabilities"})
        for command in (args.command or ["info registers"]):
            reply = talk(connection, {"execute": "human-monitor-command",
                                      "arguments": {"command-line": command}})
            text = reply.get("return") or ""
            print(f"--- {command}")
            if not text:
                print(f"(no output: {reply})")
                continue
            if args.all or args.command:
                print(text)
                continue
            for line in text.splitlines():
                if any(field in line for field in INTERESTING):
                    print(line.strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
