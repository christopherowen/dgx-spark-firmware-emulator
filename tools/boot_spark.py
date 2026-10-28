#!/usr/bin/env python3
"""Boot unchanged Spark UEFI and Standalone MM to interactive AMI setup."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import selectors
import shutil
import struct
import subprocess
import sys
import tempfile
import termios
import time
import tty

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.build_monitor import build_monitor
from tools.download_firmware import require_ignored_untracked
from tools.dxe_protocols import inspect_protocols
from tools.prepare_mm import MM_SHA256, prepare_mm
from tools.pxe import build_probe, verify_capture
from tools.run_spark import Qmp, connect_unix
from tools.spark_image import DIRECTORY, UEFI_SHA256


def boot_arguments():
    """Reserve the original monitor, real MM, communication pages and NOR image."""
    entries = [
        ("MMIO", 0, 0x40000000, 0),
        ("MMAP_MONITOR", 0x80000000, 0x100000, 0),
        ("MMAP_TINY_UEFI", 0x80100000, 0x7f00000, 1),
        ("MMAP_FREE_BASE_1", 0x88000000, 0xb3e0000, 1),
        ("MMAP_SECURE", 0x933e0000, 0xe60000, 0),
        ("MMAP_FREE_BASE_2", 0x94240000, 0xbdc0000, 1),
        ("MMAP_NOR_BACKING", 0xa0000000, 0x100000, 0),
        ("MMAP_FREE_BASE_3", 0xa0100000, 0x1ff00000, 1),
    ]
    result = bytearray(0x1870)
    struct.pack_into("<II", result, 0, 0x1868, 0x88610005)
    struct.pack_into("<I", result, 0x78, len(entries))
    for i, (name, base, size, flags) in enumerate(entries):
        offset = 0x80 + 56 * i
        result[offset:offset + len(name)] = name.encode("ascii")
        struct.pack_into("<QQI", result, offset + 32, base, size, flags)
    return bytes(result)


def local_path(path):
    path = Path(os.path.abspath(path))
    if not path.is_relative_to(ROOT / "firmware") or path.resolve() != path:
        raise ValueError("local firmware/state paths must be inside ignored firmware/, without symlinks")
    require_ignored_untracked(ROOT, path)
    return path


@contextmanager
def terminal_raw(enabled):
    saved = termios.tcgetattr(sys.stdin) if enabled else None
    try:
        if enabled:
            tty.setraw(sys.stdin.fileno())
        yield
    finally:
        if saved:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, saved)


def run(qemu, smoke, timeout, state_path, pxe=False):
    qemu = shutil.which(qemu)
    if qemu is None:
        raise ValueError("build the Spark QEMU profile first: python3 tools/build_qemu.py")
    monitor = build_monitor()
    package = prepare_mm()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    directory = local_path(ROOT / "firmware" / "runs" / stamp)
    directory.mkdir(parents=True)
    handoff = local_path(directory / "bootargs.bin")
    handoff.write_bytes(boot_arguments())
    state = local_path(state_path)
    state.parent.mkdir(parents=True, exist_ok=True)
    lock_path = local_path(state.with_suffix(state.suffix + ".lock"))
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not state.exists():
            with state.open("xb") as output:
                output.write(b"\xff" * 0x100000)
            state.chmod(0o600)
        if state.stat().st_size != 0x100000:
            raise ValueError("NOR state must be exactly 1 MiB; preserve it and select another state file")
        before = hashlib.sha256(state.read_bytes()).hexdigest()
        return run_locked(qemu, monitor, package, directory, handoff, state, before, smoke, timeout, pxe)


def run_locked(qemu, monitor, package, directory, handoff, state, before, smoke, timeout, pxe=False):
    record = {"uefi_sha256": UEFI_SHA256, "mm_sha256": MM_SHA256,
              "firmware_modified": False, "setup_visible": False,
              "keyboard_navigation_verified": False, "state_sha256_before": before,
              "state_path": str(state), "monitor_sha256": hashlib.sha256(monitor.read_bytes()).hexdigest()}
    interactive = not (smoke or pxe) and sys.stdin.isatty()
    marker = None
    if pxe:
        tftp = local_path(directory / "tftp")
        tftp.mkdir()
        token = secrets.token_hex(16)
        # Only this original application is exposed through TFTP. Logs, state,
        # vendor images and compiler outputs stay outside the served directory.
        payload = build_probe(directory, token)
        shutil.copyfile(payload, local_path(tftp / payload.name))
        marker = ("SPARK_PXE_EXECUTED:" + token).encode("ascii")
        record["pxe_execution_verified"] = False
        record["pxe_probe_sha256"] = hashlib.sha256(payload.read_bytes()).hexdigest()
    capture = bytearray()
    process = None
    partial = local_path(state.with_suffix(state.suffix + ".partial"))
    with tempfile.TemporaryDirectory(prefix="spark-") as temporary, \
            local_path(directory / "serial.log").open("wb") as log:
        command = [qemu, "-M", "virt,spark-fw-lab=on,spark-rot-error-replies=on,"
                   "gic-version=3,virtualization=on,secure=on", "-accel", "tcg",
                   "-cpu", "max,sve=off,sme=off,pauth=off", "-m", "2G", "-smp", "1",
                   "-nodefaults", "-display", "none", "-monitor", "none", "-serial", "stdio",
                   "-qmp", f"unix:{temporary}/qmp,server=on,wait=off",
                   "-d", "guest_errors,unimp", "-D", str(local_path(directory / "qemu.log"))]
        if pxe:
            command += ["-netdev", f"user,id=pxe,restrict=on,tftp={tftp},bootfile={payload.name}",
                        "-device", "spark-rtl8168,netdev=pxe,addr=1,mac=52:54:00:12:34:56",
                        "-object", "filter-dump,id=pxe-capture,netdev=pxe,file=" +
                        str(local_path(directory / "packets.pcap"))]
        else:
            command += ["-nic", "none"]
        for image, address, cpu in ((monitor, 0x80020000, True), (package, 0x93a40000, False),
                                    (DIRECTORY / "uefi.fd", 0x80800000, False),
                                    (handoff, 0x80010000, False), (state, 0xa0000000, False)):
            command += ["-device", f"loader,file={image},addr={address:#x},force-raw=on" +
                        (",cpu-num=0" if cpu else "")]
        record["command"] = command
        try:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, bufsize=0)
            client = connect_unix(Path(temporary) / "qmp", process)
            qmp = Qmp(client)
            selector = selectors.DefaultSelector()
            selector.register(process.stdout, selectors.EVENT_READ, "uart")
            if interactive:
                selector.register(sys.stdin, selectors.EVENT_READ, "keyboard")
            deadline = time.monotonic() + timeout
            escape_sent = False
            navigate_at = stop_at = None
            navigation_offset = None
            with terminal_raw(interactive):
                while process.poll() is None:
                    now = time.monotonic()
                    if now >= deadline and not (interactive and record["setup_visible"]):
                        target = "PXE download/execution" if pxe else "setup/navigation"
                        raise TimeoutError(f"{target} not reached before the boot timeout")
                    if stop_at is not None and now >= stop_at:
                        break
                    if navigate_at is not None and now >= navigate_at:
                        process.stdin.write(b"\x1b[C")
                        navigation_offset = len(capture)
                        navigate_at = None
                    for key, _ in selector.select(.05):
                        if key.data == "keyboard":
                            data = os.read(sys.stdin.fileno(), 4096)
                            if b"\x03" in data:
                                stop_at = 0
                            else:
                                process.stdin.write(data)
                            continue
                        data = os.read(process.stdout.fileno(), 65536)
                        if not data:
                            continue
                        log.write(data)
                        log.flush()
                        capture += data
                        if not (smoke or pxe):
                            sys.stdout.write(data.decode("cp437"))
                            sys.stdout.flush()
                        if b"Synchronous Exception" in capture or b"MON: abort" in capture:
                            raise RuntimeError("firmware exception; see this run's local serial.log")
                        if not pxe and not escape_sent and b"Press <DEL>" in capture:
                            process.stdin.write(b"\x1b")
                            escape_sent = True
                        if not record["setup_visible"] and b"Aptio Setup - AMI" in capture:
                            record["setup_visible"] = True
                            if smoke:
                                navigate_at = now + 1
                        if (smoke and navigation_offset is not None and
                                not record["keyboard_navigation_verified"] and
                                b"Power On Behavior" in capture[navigation_offset:]):
                            record["keyboard_navigation_verified"] = True
                            stop_at = now + 2
                        if pxe and not record["pxe_execution_verified"] and marker in capture:
                            record["pxe_execution_verified"] = True
                            stop_at = now + .5
            if process.poll() is not None:
                raise RuntimeError("QEMU exited unexpectedly; see serial.log")
            qmp.request("stop")
            if pxe:
                record["pxe_network"] = verify_capture(
                    local_path(directory / "packets.pcap").read_bytes(), payload.read_bytes())
            snapshot = local_path(directory / "dxe-memory.bin")
            qmp.request("pmemsave", {"val": 0x84000000, "size": 0x4000000,
                                     "filename": str(snapshot)})
            record["architectural_protocols"] = inspect_protocols(snapshot.read_bytes(), 0x84000000)
            if not all(p["present"] for p in record["architectural_protocols"]["protocols"]):
                raise RuntimeError("setup reached with missing architectural protocols")
            qmp.request("pmemsave", {"val": 0xa0000000, "size": 0x100000,
                                     "filename": str(partial)})
            if partial.stat().st_size != 0x100000:
                raise RuntimeError("QEMU returned a truncated NOR snapshot")
            partial.chmod(0o600)
            partial.replace(state)
            record["state_sha256_after"] = hashlib.sha256(state.read_bytes()).hexdigest()
            record["result"] = ("pxe-download-and-execution-verified" if pxe else
                                "setup-and-keyboard-verified" if smoke else "interactive-session-ended")
        except BaseException as error:
            record["error"] = str(error)
            raise
        finally:
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            partial.unlink(missing_ok=True)
            record["checkpoints"] = re.findall(rb"Checkpoint ([0-9A-F]+)", capture)
            record["checkpoints"] = [x.decode() for x in record["checkpoints"]]
            local_path(directory / "run.json").write_text(json.dumps(record, indent=2) + "\n")
    return directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", default=str(ROOT / "build/qemu-spark/qemu-system-aarch64"))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--smoke-test", action="store_true", help="verify setup and keyboard navigation, then save and stop")
    mode.add_argument("--pxe-test", action="store_true", help="verify native IPv4 PXE, exact TFTP download and EFI execution")
    parser.add_argument("--timeout", type=int, default=45, help="boot deadline in seconds")
    parser.add_argument("--state-file", type=Path, default=ROOT / "firmware/state/spark-nor.bin")
    args = parser.parse_args()
    if not 5 <= args.timeout <= 300:
        parser.error("timeout must be between 5 and 300 seconds")
    try:
        directory = run(args.qemu, args.smoke_test, args.timeout, args.state_file, args.pxe_test)
        result = "Spark PXE download and EFI execution verified" if args.pxe_test else "Spark UEFI session verified"
        print(f"\n{result}. Local logs: {directory}")
    except (OSError, ValueError, RuntimeError, TimeoutError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted before a verified state snapshot.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
