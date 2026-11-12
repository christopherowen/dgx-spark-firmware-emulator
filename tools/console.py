#!/usr/bin/env python3
"""Pace bytes into the QEMU child's serial stdin, never keyboard events over QMP.

The profile uses -nodefaults -display none -serial stdio. QMP send-key targets
keyboard handlers, not that UART, and this configuration has no keyboard handler.
"""

import time


def type_line(stream, line: str, delay: float = 0.0, sleep=time.sleep) -> int:
    """Write one UTF-8 line plus newline, flushing and pacing each serial byte."""
    data = (line + "\n").encode()
    for byte in data:
        stream.write(bytes((byte,)))
        stream.flush()
        if delay:
            sleep(delay)
    return len(data)
