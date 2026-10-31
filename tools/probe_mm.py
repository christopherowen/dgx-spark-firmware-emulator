#!/usr/bin/env python3
"""Verify the unchanged pinned STMM's fixed buffer and rejection of a shared outside pointer."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.boot_spark import local_path
from tools.build_monitor import build_monitor, find_tool
from tools.handoff import boot_arguments
from tools.prepare_mm import prepare_mm, MM_SHA256
from tools.qemu_control import GdbRemote, connect_unix


def build_probe(directory):
    objects = []
    for source in ("memory_probe_start.S", "stmm_buffer_probe.c"):
        obj = local_path(directory / (source + ".o"))
        subprocess.run([find_tool("clang"), "--target=aarch64-none-elf", "-O2", "-ffreestanding",
                        "-fno-builtin", "-fno-stack-protector", "-mstrict-align",
                        *([] if source.endswith(".S") else ["-mgeneral-regs-only"]),
                        "-Wall", "-Wextra", "-Werror", "-c",
                        str(ROOT / "tests/fixtures" / source), "-o", str(obj)], check=True)
        objects.append(str(obj))
    elf, binary = [local_path(directory / ("ns-probe" + suffix)) for suffix in (".elf", ".bin")]
    subprocess.run([find_tool("ld.lld"), "-Ttext=0x80800000", "-e", "_start", *objects,
                    "-o", str(elf)], check=True)
    subprocess.run([find_tool("llvm-objcopy"), "-O", "binary", str(elf), str(binary)], check=True)
    return binary


def run(qemu, timeout):
    qemu = shutil.which(qemu)
    if qemu is None:
        raise ValueError("build the Spark QEMU profile first: python3 tools/build_qemu.py")
    monitor, package = build_monitor(), prepare_mm()
    # The pinned package has one HVC instruction. Observe it with an execution
    # breakpoint; never patch the image, its checks or its return registers.
    image = package.read_bytes()
    hvc = bytes.fromhex("020000d4")
    offset = image.find(hvc)
    if offset < 0 or image.find(hvc, offset + 1) >= 0:
        raise ValueError("expected exactly one HVC in the pinned STMM package")
    breakpoint = 0x93a40000 + offset
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    directory = local_path(ROOT / "firmware/runs" / (stamp + "-mm-buffer"))
    directory.mkdir(parents=True)
    probe = build_probe(directory)
    handoff, nor = [local_path(directory / name) for name in ("bootargs.bin", "nor.bin")]
    handoff.write_bytes(boot_arguments())
    nor.write_bytes(b"\xff" * 0x100000)
    nor.chmod(0o600)
    record = {"mm_sha256": MM_SHA256, "firmware_modified": False,
              "monitor_sha256": hashlib.sha256(monitor.read_bytes()).hexdigest(),
              "probe_sha256": hashlib.sha256(probe.read_bytes()).hexdigest(),
              "firmware_hvc_address": hex(breakpoint)}
    process = None
    try:
        with tempfile.TemporaryDirectory(prefix="spark-mm-") as temporary, \
                local_path(directory / "serial.log").open("wb") as serial:
            command = [qemu, "-M", "virt,spark-fw-lab=on,spark-rot-error-replies=on,"
                       "gic-version=3,virtualization=on,secure=on", "-accel", "tcg",
                       "-cpu", "max,sve=off,sme=off,pauth=off", "-m", "2G", "-smp", "1",
                       "-nodefaults", "-display", "none", "-monitor", "none", "-serial", "stdio",
                       "-nic", "none", "-S", "-gdb", f"unix:{temporary}/gdb,server=on,wait=off"]
            for path, address, cpu in ((monitor, 0x80020000, True), (package, 0x93a40000, False),
                                       (probe, 0x80800000, False), (handoff, 0x80010000, False),
                                       (nor, 0xa0000000, False)):
                command += ["-device", f"loader,file={path},addr={address:#x},force-raw=on" +
                            (",cpu-num=0" if cpu else "")]
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=serial,
                                       stderr=subprocess.STDOUT)
            with GdbRemote(connect_unix(Path(temporary) / "gdb", process, timeout)) as gdb:
                packet = f"1,{breakpoint:x},4"
                if gdb.request("Z" + packet) != "OK":
                    raise RuntimeError("QEMU refused the STMM execution breakpoint")
                deadline = time.monotonic() + timeout
                for _ in range(1024):
                    if time.monotonic() >= deadline:
                        raise TimeoutError("STMM rejection not reached before the deadline")
                    stop = gdb.request("c")
                    if not stop.startswith(("T05", "S05")) or gdb.pc() != breakpoint:
                        raise RuntimeError("guest stopped outside the STMM HVC breakpoint")
                    fid = int.from_bytes(bytes.fromhex(gdb.request("p0")), "little")
                    if fid == 0x84000060:
                        registers = [int.from_bytes(bytes.fromhex(gdb.request(f"p{i:x}")), "little")
                                     for i in range(8)]
                        if (registers[1] != 0 or registers[2] != 0xfffffffffffffffd or
                                registers[3] != 0xc4000041 or registers[5] != 0x88002000):
                            raise RuntimeError("unexpected STMM error instead of outside-buffer rejection")
                        record["firmware_rejection_registers"] = [hex(x) for x in registers]
                        break
                    if gdb.request("z" + packet) != "OK" or not gdb.request("s").startswith(("T05", "S05")):
                        raise RuntimeError("could not step the STMM HVC")
                    if gdb.request("Z" + packet) != "OK":
                        raise RuntimeError("could not restore the STMM breakpoint")
                else:
                    raise RuntimeError("too many FF-A calls without a verified STMM rejection")
            serial.flush()
            capture = (directory / "serial.log").read_bytes()
            for marker in (b"MM BUFFER DISCOVERED 0x933e0000 0x20000", b"MM OUTSIDE REGION SHARED",
                           b"MM FIXED BUFFER ACCEPTED", b"MM OUTSIDE BUFFER REQUEST"):
                if marker not in capture:
                    raise RuntimeError("missing probe evidence: " + marker.decode())
            if b"MM BUFFER FAIL" in capture or b"MON: abort" in capture:
                raise RuntimeError("probe or firmware aborted")
            record.update(result="stmm-fixed-buffer-and-outside-rejection-verified",
                          ns_buffer_base="0x933e0000", ns_buffer_size=0x20000,
                          outside_region_share_succeeded=True, outside_pointer_rejected_by_firmware=True)
    except BaseException as error:
        record["error"] = str(error)
        raise
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        local_path(directory / "run.json").write_text(json.dumps(record, indent=2) + "\n")
    return directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", default=str(ROOT / "build/qemu-spark/qemu-system-aarch64"))
    parser.add_argument("--timeout", type=int, default=15)
    args = parser.parse_args()
    if not 5 <= args.timeout <= 60:
        parser.error("timeout must be between 5 and 60 seconds")
    try:
        directory = run(args.qemu, args.timeout)
        print(f"Unchanged STMM buffer checks verified. Local evidence: {directory}")
    except (OSError, ValueError, RuntimeError, TimeoutError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
