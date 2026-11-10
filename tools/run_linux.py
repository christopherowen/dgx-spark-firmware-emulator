#!/usr/bin/env python3
"""Boot a Linux kernel and initramfs under this QEMU build, and record what it printed.

The lab profile exists to run firmware. Running a whole Linux image — the vendor's recovery
environment first — is the next step, and it needs the same discipline as a firmware run: a
record of what was booted, deterministic arguments, and a log that says how far it got.

Two machines are supported:

  virt          QEMU's plain virt machine with the kernel booted directly (no firmware)
  spark-fw-lab  this repository's profile, which needs a firmware image to hand over to Linux

usage:
    run_linux.py --kernel Image --initrd initrd.gz --append "console=ttyAMA0"
    run_linux.py --recovery firmware/downloads/dgx-spark-recovery-1.155.49.tar.gz --timeout 120
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "firmware" / "downloads" / "recovery-extract"
KERNEL_MEMBERS = ("usbimg.customer/usb/vmlinuz", "usbimg.customer/usb/initrd")


def local_path(path: Path) -> Path:
    """Keep every artefact under the ignored firmware tree, as the rest of the harness does."""
    resolved = path.resolve()
    allowed = (ROOT / "firmware").resolve()
    if allowed not in resolved.parents and resolved != allowed:
        raise ValueError(f"{path} must live under firmware/, not {resolved}")
    return resolved


def extract_recovery(archive: Path) -> tuple[Path, Path]:
    """Pull the recovery kernel and initramfs out of the vendor archive, once.

    The archive is 6.4 GB and streaming it costs about a minute, so the two members are cached
    beside it. Nothing is written into the repository proper.
    """
    archive = local_path(archive)
    CACHE.mkdir(parents=True, exist_ok=True)
    kernel = CACHE / "vmlinuz"
    initrd = CACHE / "initrd"
    if kernel.is_file() and initrd.is_file():
        return kernel, initrd
    with tarfile.open(archive, "r:gz") as tar:
        for member_name, target in zip(KERNEL_MEMBERS, (kernel, initrd)):
            member = tar.getmember(member_name)
            source = tar.extractfile(member)
            if source is None:
                raise ValueError(f"{member_name} is not a regular member")
            partial = target.with_suffix(".partial")
            with partial.open("wb") as output:
                shutil.copyfileobj(source, output, 1024 * 1024)
            partial.replace(target)
    return kernel, initrd


def gunzip_image(path: Path) -> Path:
    """A gzip-compressed `vmlinuz` is decompressed once, next to the cache."""
    if path.suffix == ".Image":
        return path
    target = path.with_suffix(".Image")
    if target.is_file():
        return target
    with path.open("rb") as source:
        magic = source.read(2)
    if magic != b"\x1f\x8b":
        return path
    partial = target.with_suffix(".partial")
    with target.open("wb") as output, subprocess.Popen(["gzip", "-dc", str(path)],
                                                       stdout=output) as process:
        if process.wait() != 0:
            raise ValueError(f"{path} is not a gzip stream")
    partial.replace(target) if partial.exists() else None
    return target


def machine_arguments(machine: str) -> list[str]:
    if machine == "virt":
        return ["-M", "virt,gic-version=3"]
    if machine == "spark-fw-lab":
        return ["-M", "virt,spark-fw-lab=on,gic-version=3,virtualization=on,secure=on"]
    raise ValueError(f"unknown machine: {machine}")


def run(qemu, kernel, initrd, append, machine="virt", memory="2G", smp=2, timeout=120, disk=None):
    qemu = shutil.which(qemu) or qemu
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    directory = local_path(ROOT / "firmware" / "runs" / f"linux-{stamp}")
    directory.mkdir(parents=True)
    command = [str(qemu), *machine_arguments(machine), "-cpu", "max", "-m", memory,
               "-smp", str(smp), "-nographic", "-no-reboot",
               "-kernel", str(kernel), "-initrd", str(initrd), "-append", append]
    if disk is not None:
        command += ["-drive", f"file={local_path(disk)},if=none,id=target,format=raw",
                    "-device", "virtio-blk-pci,drive=target"]
    record = {"machine": machine, "memory": memory, "smp": smp, "timeout": timeout,
              "append": append, "kernel_sha256": hashlib.sha256(Path(kernel).read_bytes()).hexdigest(),
              "initrd_sha256": hashlib.sha256(Path(initrd).read_bytes()).hexdigest(),
              "qemu_sha256": hashlib.sha256(Path(str(qemu)).read_bytes()).hexdigest(),
              "command": command}
    log = local_path(directory / "console.log")
    with log.open("wb") as capture:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT)
        try:
            process.wait(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            process.terminate()
            try:
                out, _ = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                out, _ = process.communicate()
            if out:
                capture.write(out)
    text = log.read_text(errors="replace")
    record["timed_out"] = timed_out
    record["lines"] = len(text.splitlines())
    for marker, key in ((r"Linux version (\S+)", "kernel_version"),
                        (r"VERSION=\"([^\"]+)\"", "image_version")):
        match = re.search(marker, text)
        if match:
            record[key] = match.group(1)
    record["reached_userspace"] = bool(re.search(r"^--- FastOS Version", text, re.M))
    record["usb_image_version"] = (re.search(r"VERSION=\"([^\"]+)\"", text).group(1)
                                   if re.search(r"VERSION=\"([^\"]+)\"", text) else None)
    record["result"] = ("userspace" if record["reached_userspace"] else
                        "kernel" if "Linux version" in text else "not-booted")
    local_path(directory / "run.json").write_text(json.dumps(record, indent=2) + "\n")
    return directory


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--qemu", default=str(ROOT / "build" / "qemu-spark" / "qemu-system-aarch64"))
    parser.add_argument("--kernel", type=Path, help="kernel Image (gzip allowed)")
    parser.add_argument("--initrd", type=Path)
    parser.add_argument("--recovery", type=Path,
                        help="the vendor recovery archive; its kernel and initramfs are used")
    parser.add_argument("--append", default="console=ttyAMA0,115200 earlycon loglevel=7")
    parser.add_argument("--machine", choices=("virt", "spark-fw-lab"), default="virt")
    parser.add_argument("--memory", default="2G")
    parser.add_argument("--smp", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--disk", type=Path, help="an image to attach as virtio-blk")
    args = parser.parse_args()
    try:
        if args.recovery:
            kernel, initrd = extract_recovery(args.recovery)
        else:
            if not args.kernel or not args.initrd:
                raise ValueError("--kernel and --initrd, or --recovery, are required")
            kernel, initrd = args.kernel, args.initrd
        kernel = gunzip_image(kernel)
        directory = run(args.qemu, kernel, initrd, args.append, args.machine, args.memory,
                        args.smp, args.timeout, args.disk)
        print(directory)
        return 0
    except (OSError, ValueError, subprocess.SubprocessError, tarfile.TarError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
