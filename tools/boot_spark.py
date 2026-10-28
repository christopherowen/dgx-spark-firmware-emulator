#!/usr/bin/env python3
"""Boot unchanged Spark UEFI/MM to AMI setup or verify native IPv4 PXE."""

import argparse
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from enum import Enum
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import selectors
import shutil
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
from tools.handoff import boot_arguments
from tools.prepare_mm import MM_SHA256, prepare_mm
from tools.pxe import build_probe, verify_capture
from tools.qemu_control import Qmp, connect_unix
from tools.spark_image import DIRECTORY, UEFI_SHA256


class RunMode(Enum):
    INTERACTIVE = "interactive-session-ended"
    SETUP_TEST = "setup-and-keyboard-verified"
    PXE_TEST = "pxe-download-and-execution-verified"


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


def run(qemu, mode, timeout, state_path):
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
        return run_locked(qemu, monitor, package, directory, handoff, state, before, mode, timeout)


def run_locked(qemu, monitor, package, directory, handoff, state, before, mode, timeout):
    record = {"mode": mode.name.lower(), "uefi_sha256": UEFI_SHA256, "mm_sha256": MM_SHA256,
              "firmware_modified": False, "setup_visible": False,
              "keyboard_navigation_verified": False, "state_sha256_before": before,
              "state_path": str(state), "monitor_sha256": hashlib.sha256(monitor.read_bytes()).hexdigest()}
    interactive = mode is RunMode.INTERACTIVE and sys.stdin.isatty()
    marker = None
    if mode is RunMode.PXE_TEST:
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
    with (
        tempfile.TemporaryDirectory(prefix="spark-") as temporary,
        ExitStack() as resources,
        local_path(directory / "serial.log").open("wb") as log,
    ):
        command = [qemu, "-M", "virt,spark-fw-lab=on,spark-rot-error-replies=on,"
                   "gic-version=3,virtualization=on,secure=on", "-accel", "tcg",
                   "-cpu", "max,sve=off,sme=off,pauth=off", "-m", "2G", "-smp", "1",
                   "-nodefaults", "-display", "none", "-monitor", "none", "-serial", "stdio",
                   "-qmp", f"unix:{temporary}/qmp,server=on,wait=off",
                   "-d", "guest_errors,unimp", "-D", str(local_path(directory / "qemu.log"))]
        if mode is RunMode.PXE_TEST:
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
            process = resources.enter_context(subprocess.Popen(
                command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, bufsize=0))
            qmp = resources.enter_context(Qmp(connect_unix(Path(temporary) / "qmp", process)))
            selector = resources.enter_context(selectors.DefaultSelector())
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
                        target = "PXE download/execution" if mode is RunMode.PXE_TEST else "setup/navigation"
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
                        if mode is RunMode.INTERACTIVE:
                            sys.stdout.write(data.decode("cp437"))
                            sys.stdout.flush()
                        if b"Synchronous Exception" in capture or b"MON: abort" in capture:
                            raise RuntimeError("firmware exception; see this run's local serial.log")
                        if mode is not RunMode.PXE_TEST and not escape_sent and b"Press <DEL>" in capture:
                            process.stdin.write(b"\x1b")
                            escape_sent = True
                        if not record["setup_visible"] and b"Aptio Setup - AMI" in capture:
                            record["setup_visible"] = True
                            if mode is RunMode.SETUP_TEST:
                                navigate_at = now + 1
                        if (mode is RunMode.SETUP_TEST and navigation_offset is not None and
                                not record["keyboard_navigation_verified"] and
                                b"Power On Behavior" in capture[navigation_offset:]):
                            record["keyboard_navigation_verified"] = True
                            stop_at = now + 2
                        if mode is RunMode.PXE_TEST and not record["pxe_execution_verified"] and marker in capture:
                            record["pxe_execution_verified"] = True
                            stop_at = now + .5
            if process.poll() is not None:
                raise RuntimeError("QEMU exited unexpectedly; see serial.log")
            qmp.request("stop")
            if mode is RunMode.PXE_TEST:
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
            record["result"] = mode.value
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
    parser.set_defaults(mode=RunMode.INTERACTIVE)
    checks = parser.add_mutually_exclusive_group()
    checks.add_argument("--smoke-test", dest="mode", action="store_const", const=RunMode.SETUP_TEST,
                        help="verify setup and keyboard navigation, then save and stop")
    checks.add_argument("--pxe-test", dest="mode", action="store_const", const=RunMode.PXE_TEST,
                        help="verify native IPv4 PXE, exact TFTP download and EFI execution")
    parser.add_argument("--timeout", type=int, default=45, help="boot deadline in seconds")
    parser.add_argument("--state-file", type=Path, default=ROOT / "firmware/state/spark-nor.bin")
    args = parser.parse_args()
    if not 5 <= args.timeout <= 300:
        parser.error("timeout must be between 5 and 300 seconds")
    try:
        directory = run(args.qemu, args.mode, args.timeout, args.state_file)
        result = ("Spark PXE download and EFI execution verified" if args.mode is RunMode.PXE_TEST
                  else "Spark UEFI session verified")
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
