"""Small QMP and GDB clients shared by the boot runner and diagnostics."""

from contextlib import AbstractContextManager
import json
import re
import socket
import time


def connect_unix(path, process, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        connection = socket.socket(socket.AF_UNIX)
        connection.settimeout(timeout)
        try:
            connection.connect(str(path))
            return connection
        except (FileNotFoundError, ConnectionRefusedError):
            connection.close()
            if process.poll() is not None:
                raise RuntimeError("QEMU exited before its debug connection opened; see serial.log")
            time.sleep(0.05)
    raise TimeoutError("QEMU debug connection did not open")


class GdbRemote(AbstractContextManager):
    def __init__(self, connection):
        self.connection = connection

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self.connection.close()

    def send(self, command):
        payload = command.encode("ascii")
        self.connection.sendall(b"$" + payload + b"#" + f"{sum(payload) & 255:02x}".encode())

    def request(self, command):
        self.send(command)
        while True:
            value = self.connection.recv(1)
            if not value:
                raise RuntimeError("QEMU closed the GDB connection")
            if value == b"$":
                break
        encoded = bytearray()
        while (value := self.connection.recv(1)) != b"#":
            if not value:
                raise RuntimeError("truncated GDB packet")
            encoded += value
        checksum = b""
        while len(checksum) < 2:
            chunk = self.connection.recv(2 - len(checksum))
            if not chunk:
                raise RuntimeError("truncated GDB checksum")
            checksum += chunk
        if int(checksum, 16) != sum(encoded) & 255:
            raise RuntimeError("GDB packet checksum mismatch")
        self.connection.sendall(b"+")
        decoded = bytearray()
        escaped = False
        for value in encoded:
            if escaped:
                decoded.append(value ^ 0x20)
                escaped = False
            elif value == 0x7d:
                escaped = True
            else:
                decoded.append(value)
        return decoded.decode("ascii")

    def pc(self):
        reply = self.request("p20")
        if not re.fullmatch("[0-9a-fA-F]{16}", reply):
            raise RuntimeError(f"could not read AArch64 PC: {reply}")
        return int.from_bytes(bytes.fromhex(reply), "little")

    def checkpoint(self, address):
        packet = f"1,{address:x},4"
        if self.request("Z" + packet) != "OK":
            raise RuntimeError("QEMU refused the execution breakpoint")
        stop = self.request("c")
        actual = self.pc()
        if not stop.startswith(("T05", "S05")) or actual != address:
            raise RuntimeError(f"checkpoint {address:#x} not reached; stopped at {actual:#x}")
        if self.request("z" + packet) != "OK":
            raise RuntimeError("QEMU refused to remove the execution breakpoint")
        self.request("s")
        after = self.pc()
        if after != address + 4:
            raise RuntimeError("checkpoint instruction did not advance to its expected successor")
        return {"entry_pc": hex(address), "pc_after_single_step": hex(after)}


class Qmp(AbstractContextManager):
    def __init__(self, connection):
        self.connection = connection
        self.stream = connection.makefile("rwb", buffering=0)
        try:
            if "QMP" not in json.loads(self.stream.readline()):
                raise RuntimeError("missing QMP greeting")
            self.request("qmp_capabilities")
        except BaseException:
            self.close()
            raise

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self.stream.close()
        self.connection.close()

    def request(self, command, arguments=None):
        self.stream.write((json.dumps({"execute": command, "arguments": arguments or {}}) + "\n").encode())
        while True:
            line = self.stream.readline()
            if not line:
                raise RuntimeError("QEMU closed the QMP connection")
            response = json.loads(line)
            if "error" in response:
                raise RuntimeError(str(response["error"]))
            if "return" in response:
                return response["return"]
