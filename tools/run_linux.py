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
import select
import shutil
import subprocess
import sys
import tarfile
import pathlib
import threading
import time

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


def die_with_parent():
    """Have the child take SIGKILL when this process dies.

    A boot that is killed -- by a job runner, by a timeout, by hand -- otherwise leaves QEMU running:
    it keeps the state file locked, so every later launch fails with `[Errno 11]` from the lock and
    silently reports nothing, and it keeps burning a core while the serial logs it writes belong to a
    run nobody is watching any more.
    """
    try:
        import ctypes
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(1, 9, 0, 0, 0)  # PR_SET_PDEATHSIG, SIGKILL
    except (OSError, AttributeError):
        pass

def type_script(stream, script: pathlib.Path, after: float, delay: float) -> None:
    """Type a console script into the guest, one line at a time, after `after` seconds.

    With `-nographic` QEMU's serial is stdio, so a line written to the child's stdin is a line the
    guest receives. The delay matters: the guest's UART FIFO is small and TCG is slow, so the lines
    must not arrive faster than the shell can drain them.
    """
    time.sleep(after)
    for line in script.read_text().splitlines():
        try:
            stream.write((line + "\n").encode())
            stream.flush()
        except (BrokenPipeError, ValueError, OSError):
            # The guest exited or the reader closed while we were still typing: that is the
            # run's outcome to judge, not an error to raise from a background thread.
            return
        time.sleep(delay)
    # The stream is deliberately left open: Popen.communicate() flushes and closes it, and closing
    # it here makes that flush raise "flush of closed file" instead of returning the run's output.


def run(qemu, kernel, initrd, append, machine="virt", memory="2G", smp=2, timeout=120, disk=None,
        usb_image=None, target=None, console_script=None, console_after=0.0, console_delay=0.5,
        console_wait=None):
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
    # The installer mounts its media from /dev/sd?? (or /dev/sr0), and the recovery initramfs
    # modprobes xhci-plat-hcd, which needs a platform device with a firmware description. This
    # machine has no such node, so a usb-storage device cannot be reached. A virtio-scsi disk
    # appears as /dev/sda, which is the device name the installer's globs and mounts accept.
    if usb_image is not None:
        usb_image = local_path(usb_image)
        command += ["-device", "virtio-scsi-pci,id=media",
                    "-drive", f"if=none,id=media0,file={usb_image},format=raw,snapshot=on",
                    "-device", "scsi-hd,drive=media0,bus=media.0,serial=SPARKKEY"]
    # init_mount partitions /dev/nvme0n1 by name, so the install target has to be an nvme device.
    # No snapshot here: the installer writes the root image into this disk, and a boot of the
    # result is the only proof the install finished. The media keeps its snapshot because a run
    # must never modify the payload it was given.
    if target is not None:
        target = local_path(target)
        command += ["-drive", f"if=none,id=target,file={target},format=raw",
                    "-device", "nvme,drive=target,serial=SPARKTGT"]
    record = {"machine": machine, "memory": memory, "smp": smp, "timeout": timeout,
              "append": append, "kernel_sha256": hashlib.sha256(Path(kernel).read_bytes()).hexdigest(),
              "initrd_sha256": hashlib.sha256(Path(initrd).read_bytes()).hexdigest(),
              "qemu_sha256": hashlib.sha256(Path(str(qemu)).read_bytes()).hexdigest(),
              "command": command}
    if target is not None:
        record["target"] = {"path": str(target), "size": target.stat().st_size,
                            "write_policy": "written by the guest: the install lands here"}
    if usb_image is not None:
        record["usb_image"] = {"path": str(usb_image),
                               "sha256": hashlib.sha256(usb_image.read_bytes()).hexdigest(),
                               "write_policy": "snapshot; the source image is never written"}
    log = local_path(directory / "console.log")
    typing = None
    typed = False
    started = time.monotonic()
    deadline_at = started + timeout
    timed_out = False
    marker = console_wait.encode() if console_wait else None
    with log.open("wb") as capture:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE if console_script else subprocess.DEVNULL,
                preexec_fn=die_with_parent,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if console_script:
            record["console_script"] = {"path": str(console_script), "typed_after": console_after,
                                        "waited_for": console_wait,
                                        "lines": len(console_script.read_text().splitlines())}

        def start_typing() -> None:
            """Type once. A marker is the only reliable trigger: the guest decides when its shell
            exists, and a fixed delay races it -- a slow boot loses the script entirely."""
            nonlocal typing, typed
            if typed:
                return
            typed = True
            typing = threading.Thread(target=type_script,
                                      args=(process.stdin, console_script, 0, console_delay),
                                      daemon=True)
            typing.start()
            record.setdefault("console_script", {})["typed_at"] = round(
                time.monotonic() - started, 3)

        seen = bytearray()
        while process.poll() is None:
            now = time.monotonic()
            if now >= deadline_at:
                timed_out = True
                break
            ready, _, _ = select.select([process.stdout], [], [], 0.1)
            if not ready:
                if console_script and not typed and marker is None and now - started >= console_after:
                    start_typing()
                continue
            data = os.read(process.stdout.fileno(), 65536)
            if not data:
                break
            capture.write(data)
            capture.flush()
            if console_script and not typed:
                if marker is None:
                    if now - started >= console_after:
                        start_typing()
                else:
                    seen += data
                    if marker in seen:
                        start_typing()
                    elif len(seen) > 1 << 20:
                        del seen[: len(seen) - len(marker)]
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
    parser.add_argument("--console-script", type=Path,
                        help="type this file's lines into the guest console, one line per Enter")
    parser.add_argument("--console-after", type=float, default=0.0,
                        help="seconds to wait before typing the console script")
    parser.add_argument("--console-wait", metavar="TEXT",
                        help="hold the console script until this text appears in the guest output; "
                             "a marker beats a delay because a slow boot otherwise loses the script")
    parser.add_argument("--console-delay", type=float, default=0.5,
                        help="seconds between typed lines; the guest UART is small and TCG is slow")
    parser.add_argument("--target", type=Path,
                        help="the install target: init_mount partitions /dev/nvme0n1 by name")
    parser.add_argument("--usb-image", type=Path,
                        help="the recovery media as a USB key: recovery init mounts the installer "
                             "from a USB device, not from the initramfs")
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
                        args.smp, args.timeout, args.disk, args.usb_image, args.target,
                        args.console_script, args.console_after, args.console_delay,
                        args.console_wait)
        print(directory)
        return 0
    except (OSError, ValueError, subprocess.SubprocessError, tarfile.TarError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
