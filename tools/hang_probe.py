#!/usr/bin/env python3
"""Sample a boot that never reaches setup and localise where it is stuck.

Boots a state file, waits, then interrupts the guest repeatedly over QMP to read
the program counter, and optionally dumps the code window and the buffers named
by the argument registers.  Matching the sampled code back into a firmware image
is then a byte search, which is how the checkpoint-61 hang was located.

Requires `cpu-num=0` on the monitor loader: without it the CPU never leaves the
reset vector and every sample reads 0x200.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.boot_spark import boot_arguments, build_monitor, prepare_mm, resolve_nor_size
from tools.spark_image import DEFAULT_VERSION, VERSIONS, prepared_directory
from tools.module_map import locate, module_at
from tools.qemu_control import Qmp, connect_unix

FIRMWARE_BASE = 0x80800000


def parse_pc(registers: str) -> int | None:
    for line in registers.splitlines():
        if line.strip().startswith("PC="):
            return int(line.split("=")[1].split()[0], 16)
    return None


def parse_registers(registers: str) -> dict[int, int]:
    values: dict[int, int] = {}
    for line in registers.splitlines():
        for token in line.replace("|", " ").split():
            if token.startswith("X") and "=" in token:
                name, value = token.split("=", 1)
                if name[1:].isdigit():
                    values[int(name[1:])] = int(value, 16)
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state", type=Path, help="NOR state to boot")
    parser.add_argument("--wait", type=float, default=70.0, help="seconds before sampling")
    parser.add_argument("--samples", type=int, default=8)
    parser.add_argument("--interval", type=float, default=0.7)
    parser.add_argument("--dump", action="append", default=[],
                        metavar="ADDR:SIZE", help="guest range to save, hex, repeatable")
    parser.add_argument("--out", type=Path, help="directory for samples and dumps")
    parser.add_argument("--firmware-version", default=DEFAULT_VERSION, choices=sorted(VERSIONS),
                        help="SoC release to boot; the board ran 2.155.11")
    parser.add_argument("--firmware", type=Path,
                        help="firmware image used to name the module a sampled PC lands in; "
                             "defaults to the prepared image for --firmware-version")
    arguments = parser.parse_args()

    state = arguments.state.resolve()
    if not state.is_file():
        raise SystemExit(f"no such state file: {state}")
    try:
        state_size = resolve_nor_size(state)
    except ValueError as error:
        raise SystemExit(str(error)) from None
    monitor, package = (build_monitor("armed", nor_size=state_size),
                        prepare_mm(arguments.firmware_version))
    firmware_directory = prepared_directory(arguments.firmware_version)
    temporary = Path(tempfile.mkdtemp(prefix="hang-probe-"))
    arguments.out_dir = arguments.out or (temporary / "samples")
    arguments.out_dir.mkdir(parents=True, exist_ok=True)
    firmware_path = arguments.firmware or (firmware_directory / "uefi.fd")
    firmware_image = firmware_path.read_bytes() if firmware_path.is_file() else None
    handoff = temporary / "bootargs.bin"
    handoff.write_bytes(boot_arguments(state_size))
    # The lab machine only exists in the built profile; a stock system binary
    # would reject -M virt,spark-fw-lab=on.
    qemu = str(ROOT / "build/qemu-spark/qemu-system-aarch64")
    if not Path(qemu).is_file():
        raise SystemExit("build the Spark QEMU profile first: python3 tools/build_qemu.py")
    command = [qemu, "-M", "virt,spark-fw-lab=on,spark-rot-error-replies=on,gic-version=3,"
               "virtualization=on,secure=on", "-accel", "tcg",
               "-cpu", "max,sve=off,sme=off,pauth=off", "-m", "2G", "-smp", "1",
               "-nodefaults", "-display", "none", "-monitor", "none", "-nic", "none",
               "-serial", "stdio", "-qmp", f"unix:{temporary}/qmp,server=on,wait=off"]
    for image, address, cpu in ((monitor, 0x80020000, True), (package, 0x93a40000, False),
                                (firmware_directory / "uefi.fd", FIRMWARE_BASE, False),
                                (handoff, 0x80010000, False), (state, 0xa0000000, False)):
        command += ["-device", f"loader,file={image},addr={address:#x},force-raw=on"
                    + (",cpu-num=0" if cpu else "")]
    serial = (temporary / "serial.log").open("wb")
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=serial,
                               stderr=subprocess.STDOUT)
    try:
        qmp = Qmp(connect_unix(temporary / "qmp", process))
        time.sleep(arguments.wait)
        samples, registers = [], None
        for _ in range(arguments.samples):
            info = qmp.request("human-monitor-command", {"command-line": "info registers"})
            samples.append(parse_pc(info))
            registers = parse_registers(info)
            time.sleep(arguments.interval)
        unique = list(dict.fromkeys(samples))
        print("PC samples:", [f"{s:#x}" if s is not None else "?" for s in samples])
        for address in unique:
            if address is None:
                continue
            if FIRMWARE_BASE <= address < FIRMWARE_BASE + 0x2000000:
                print(f"  {address:#x} -> uefi.fd offset {address - FIRMWARE_BASE:#x}")
                continue
            # The code runs from RAM, so match its bytes back into the image and
            # name the module the decompiler should open.
            window = arguments.out_dir / f"code-{address:#x}.bin"
            qmp.request("pmemsave", {"val": address - 0x40, "size": 0xA0, "filename": str(window)})
            if firmware_image is None:
                print(f"  {address:#x} -> outside the firmware image; no --firmware given")
                continue
            blob = window.read_bytes()
            # Relocated pointers inside the first instructions can defeat a byte
            # match, so try the window starting at the sampled address as well.
            # The dump starts 0x40 bytes before the sampled PC, so the match must be turned back
            # into an offset for the PC itself: matching the whole window puts the PC 0x40 bytes in,
            # while matching the window from the PC puts it at index zero.
            hit = locate(firmware_image, blob)
            pc_index = 0x40
            if hit is None:
                hit = locate(firmware_image, blob[0x40:])
                pc_index = 0
            if hit is None:
                print(f"  {address:#x} -> code not found in the firmware image")
                continue
            offset, length, skipped = hit
            offset += pc_index - skipped
            module = module_at(firmware_image, offset)
            print(f"  {address:#x} -> image {offset:#x} ({length} bytes matched) -> "
                  + (module.label() if module else "not inside any module"))
        if registers:
            print("pointer registers:", {f"x{k}": hex(v) for k, v in registers.items()
                                         if 0x80000000 <= v < 0xa0000000})
        if arguments.out:
            arguments.out.mkdir(parents=True, exist_ok=True)
            for spec in arguments.dump:
                address_text, size_text = spec.split(":")
                address, size = int(address_text, 16), int(size_text, 16)
                target = arguments.out / f"dump-{address:#x}.bin"
                qmp.request("pmemsave", {"val": address, "size": size, "filename": str(target)})
                print(f"  dumped {address:#x}+{size:#x} -> {target}")
        text = (temporary / "serial.log").read_text(errors="ignore")
        print("checkpoints:", text.count("Checkpoint"),
              "| last:", [l for l in text.splitlines() if l.strip()][-1:])
        return 0
    finally:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
        serial.close()
        if arguments.out is not None:
            shutil.rmtree(temporary, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
