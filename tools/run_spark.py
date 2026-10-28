#!/usr/bin/env python3
"""Execute unchanged Spark SEC and DXE entries under QEMU, then optionally continue."""

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import json
import lzma
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.download_firmware import require_ignored_untracked
from tools.dxe_protocols import inspect_protocols
from tools.handoff import diagnostic_boot_arguments
from tools.qemu_control import GdbRemote, Qmp, connect_unix
from tools.spark_image import prepare


def run(qemu, seconds, experimental, rot_error_replies=False):
    image, layout = prepare()
    qemu = shutil.which(qemu)
    if qemu is None:
        raise ValueError("QEMU executable not found")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    output = ROOT / "firmware" / "runs" / stamp
    if output.resolve() != output:
        raise ValueError("run output must not traverse symbolic links")
    output.mkdir(parents=True)
    for name in ("bootargs.bin", "serial.log", "trace.log", "registers.txt",
                 "run.json", "dxe-core-memory.bin"):
        require_ignored_untracked(ROOT, output / name)
    handoff = output / "bootargs.bin"
    handoff.write_bytes(diagnostic_boot_arguments())
    record = {"firmware": layout, "synthetic_loader_handoff": True,
              "firmware_modified": False, "full_boot_verified": False,
              "experimental_address_profile": experimental,
              "rot_service_mode": "error-replies" if rot_error_replies else "unimplemented",
              "checkpoints": {}}
    with tempfile.TemporaryDirectory(prefix="spark-qemu-", dir="/tmp") as sockets:
        qmp_path, gdb_path = Path(sockets) / "qmp", Path(sockets) / "gdb"
        machine = "virt,virtualization=on,gic-version=3"
        if experimental:
            machine += ",spark-fw-lab=on"
        if rot_error_replies:
            machine += ",spark-rot-error-replies=on"
        command = [qemu, "-M", machine, "-accel", "tcg", "-cpu", "max", "-smp", "1", "-m", "2G",
                   "-nodefaults", "-display", "none", "-monitor", "none", "-serial", "stdio",
                   "-nic", "none", "-S", "-qmp", f"unix:{qmp_path},server=on,wait=off",
                   "-gdb", f"unix:{gdb_path},server=on,wait=off",
                   "-device", f"loader,file={image},addr={layout['firmware_base']:#x},cpu-num=0,force-raw=on",
                   "-device", f"loader,file={handoff},addr=0x80010000,force-raw=on",
                   "-trace", "enable=arm_psci_call",
                   "-d", "in_asm,guest_errors,unimp", "-D", str(output / "trace.log")]
        record["command"] = command
        record["qemu_version"] = subprocess.check_output([qemu, "--version"], text=True).splitlines()[0]
        with (output / "serial.log").open("wb") as serial:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=serial, stderr=subprocess.STDOUT)
            resources = ExitStack()
            try:
                qmp = resources.enter_context(Qmp(connect_unix(qmp_path, process)))
                gdb = resources.enter_context(GdbRemote(connect_unix(gdb_path, process, timeout=30)))
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
                if seconds:
                    # Only the known, checkpoint-verified DXE allocation is read.
                    # The snapshot contains vendor-derived bytes and stays ignored.
                    snapshot = output / "dxe-core-memory.bin"
                    try:
                        qmp.request("pmemsave", {"val": 0x847c0000,
                                                "size": layout["dxe_image_size"],
                                                "filename": str(snapshot)})
                        record["architectural_protocols"] = inspect_protocols(
                            snapshot.read_bytes(), 0x847c0000)
                    except (OSError, ValueError, RuntimeError) as error:
                        record["protocol_inspection_error"] = str(error)
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
                resources.close()
                (output / "run.json").write_text(json.dumps(record, indent=2) + "\n")
                print(f"Local run evidence: {output}", flush=True)
    serial_text = (output / "serial.log").read_text(errors="replace")
    # The virt PSCI conduit receives SMCs even when their function is FF-A
    # or vendor-specific. This records requests, not successful services.
    smc_ids = set()
    with (output / "trace.log").open(errors="replace") as trace:
        for line in trace:
            match = re.search(r"arm_psci_call .*x0=(0x[0-9a-fA-F]+)", line)
            if match:
                smc_ids.add(int(match.group(1), 16))
    record["smc_function_ids"] = [hex(value) for value in sorted(smc_ids)]
    record["serial_checkpoint_61"] = "Checkpoint 61" in serial_text
    record["serial_checkpoints"] = re.findall(r"Checkpoint ([0-9A-Fa-f]+)", serial_text)
    protocols = record.get("architectural_protocols", {}).get("protocols", [])
    missing = [protocol["name"] for protocol in protocols if not protocol["present"]]
    if missing:
        print("Missing architectural protocols: " + ", ".join(missing), flush=True)
    fault = re.search(r"Synchronous Exception at (0x[0-9a-fA-F]+)", serial_text)
    if fault:
        record["firmware_exception_pc"] = fault.group(1)
    (output / "run.json").write_text(json.dumps(record, indent=2) + "\n")
    if fault:
        raise RuntimeError(f"firmware stopped at exception {fault.group(1)}; full boot is incomplete")
    print("This diagnostic stops before MM/BDS. Use tools/boot_spark.py for full UEFI boot.")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", default="qemu-system-aarch64")
    parser.add_argument("--continue-seconds", type=int, default=0,
                        help="continue after DXE entry for 0..60 seconds, then capture registers")
    parser.add_argument("--experimental-address-profile", action="store_true",
                        help="use the locally patched QEMU interrupt/serial address profile")
    parser.add_argument("--rot-error-replies", action="store_true",
                        help="opt-in ROT failure scenario: unavailable services return error replies")
    args = parser.parse_args()
    if not 0 <= args.continue_seconds <= 60:
        parser.error("continue-seconds must be between 0 and 60")
    if args.rot_error_replies and not args.experimental_address_profile:
        parser.error("rot-error-replies requires experimental-address-profile")
    try:
        run(args.qemu, args.continue_seconds, args.experimental_address_profile, args.rot_error_replies)
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, lzma.LZMAError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
