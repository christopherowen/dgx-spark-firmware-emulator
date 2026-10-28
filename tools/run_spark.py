#!/usr/bin/env python3
"""Execute unchanged Spark SEC and DXE entries under QEMU, then optionally continue."""

import argparse
from datetime import datetime, timezone
import json
import lzma
from pathlib import Path
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.spark_image import prepare


def boot_arguments():
    """A synthetic loader handoff for a 1 GiB RAM scenario; not a hardware capture."""
    entries = [("MMIO", 0, 0x40000000, 0),
               ("MMAP_TINY_UEFI", 0x80000000, 0x08000000, 1),
               ("MMAP_FREE_BASE_1", 0x88000000, 0x38000000, 1)]
    # SEC copies the complete 0x1868-byte memory tag into a GUID HOB.
    result = bytearray(0x1868 + 8)
    struct.pack_into("<II", result, 0, 0x1868, 0x88610005)
    struct.pack_into("<I", result, 0x78, len(entries))
    for index, (name, base, size, flags) in enumerate(entries):
        offset = 0x80 + 56 * index
        result[offset:offset + len(name)] = name.encode("ascii")
        struct.pack_into("<QQI", result, offset + 32, base, size, flags)
    return bytes(result)


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


class GdbRemote:
    def __init__(self, connection):
        self.connection = connection

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


class Qmp:
    def __init__(self, connection):
        self.stream = connection.makefile("rwb", buffering=0)
        if "QMP" not in json.loads(self.stream.readline()):
            raise RuntimeError("missing QMP greeting")
        self.request("qmp_capabilities")

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


def run(qemu, seconds, experimental):
    image, layout = prepare()
    qemu = shutil.which(qemu)
    if qemu is None:
        raise ValueError("QEMU executable not found")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    output = ROOT / "firmware" / "runs" / stamp
    if output.resolve() != output:
        raise ValueError("run output must not traverse symbolic links")
    output.mkdir(parents=True)
    handoff = output / "bootargs.bin"
    handoff.write_bytes(boot_arguments())
    record = {"firmware": layout, "synthetic_loader_handoff": True,
              "firmware_modified": False, "full_boot_verified": False,
              "experimental_address_profile": experimental, "checkpoints": {}}
    with tempfile.TemporaryDirectory(prefix="spark-qemu-", dir="/tmp") as sockets:
        qmp_path, gdb_path = Path(sockets) / "qmp", Path(sockets) / "gdb"
        machine = "virt,virtualization=on,gic-version=3"
        if experimental:
            machine += ",spark-fw-lab=on"
        command = [qemu, "-M", machine, "-accel", "tcg", "-cpu", "max", "-smp", "1", "-m", "2G",
                   "-nodefaults", "-display", "none", "-monitor", "none", "-serial", "stdio",
                   "-nic", "none", "-S", "-qmp", f"unix:{qmp_path},server=on,wait=off",
                   "-gdb", f"unix:{gdb_path},server=on,wait=off",
                   "-device", f"loader,file={image},addr={layout['firmware_base']:#x},cpu-num=0,force-raw=on",
                   "-device", f"loader,file={handoff},addr=0x80010000,force-raw=on",
                   "-d", "in_asm,guest_errors,unimp", "-D", str(output / "trace.log")]
        record["command"] = command
        record["qemu_version"] = subprocess.check_output([qemu, "--version"], text=True).splitlines()[0]
        with (output / "serial.log").open("wb") as serial:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=serial, stderr=subprocess.STDOUT)
            connections = []
            try:
                connections.append(connect_unix(qmp_path, process))
                qmp = Qmp(connections[-1])
                connections.append(connect_unix(gdb_path, process, timeout=30))
                gdb = GdbRemote(connections[-1])
                if gdb.request("P0=" + (0x80010000).to_bytes(8, "little").hex()) != "OK":
                    raise RuntimeError("could not set the loader's x0 handoff pointer")
                record["checkpoints"]["sec"] = gdb.checkpoint(layout["sec_entry"])
                # This allocation is deterministic for this pinned firmware and handoff.
                # A changed allocation must fail, rather than invent a successful checkpoint.
                dxe_entry = 0x847c0000 + layout["dxe_entry_rva"]
                record["checkpoints"]["dxe_core"] = gdb.checkpoint(dxe_entry)
                print("PASS: real Spark SEC and DXE core entry instructions executed", flush=True)
                if seconds:
                    gdb.send("c")
                    time.sleep(seconds)
                    qmp.request("stop")
                registers = qmp.request("human-monitor-command", {"command-line": "info registers"})
                (output / "registers.txt").write_text(registers)
                record["continued_seconds"] = seconds
            except Exception as error:
                record["error"] = str(error)
                raise
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                for connection in connections:
                    connection.close()
                (output / "run.json").write_text(json.dumps(record, indent=2) + "\n")
                print(f"Local run evidence: {output}", flush=True)
    serial_text = (output / "serial.log").read_text(errors="replace")
    record["serial_checkpoint_61"] = "Checkpoint 61" in serial_text
    fault = re.search(r"Synchronous Exception at (0x[0-9a-fA-F]+)", serial_text)
    if fault:
        record["firmware_exception_pc"] = fault.group(1)
    (output / "run.json").write_text(json.dumps(record, indent=2) + "\n")
    if fault:
        raise RuntimeError(f"firmware stopped at exception {fault.group(1)}; full boot is incomplete")
    print("Full Spark firmware boot is not yet verified.")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", default="qemu-system-aarch64")
    parser.add_argument("--continue-seconds", type=int, default=0,
                        help="continue after DXE entry for 0..60 seconds, then capture registers")
    parser.add_argument("--experimental-address-profile", action="store_true",
                        help="use the locally patched QEMU interrupt/serial address profile")
    args = parser.parse_args()
    if not 0 <= args.continue_seconds <= 60:
        parser.error("continue-seconds must be between 0 and 60")
    try:
        run(args.qemu, args.continue_seconds, args.experimental_address_profile)
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, lzma.LZMAError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
