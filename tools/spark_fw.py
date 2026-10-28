#!/usr/bin/env python3
"""Local firmware inventory and a generic QEMU UEFI baseline; standard library only."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import uuid


FMP_GUID = uuid.UUID("6dcbd5ed-e82d-4c44-bda1-7194199ad92a")
ELF_MACHINES = {3: "x86", 40: "Arm", 62: "x86-64", 183: "AArch64", 243: "RISC-V"}
PE_MACHINES = {0x14C: "x86", 0x1C4: "Arm Thumb-2", 0x8664: "x86-64", 0xAA64: "AArch64"}


class FormatError(ValueError):
    """A recognized container has an invalid or truncated header."""


def read_at(stream, offset, length, size):
    if offset < 0 or offset + length > size:
        raise FormatError("truncated header or out-of-bounds header offset")
    stream.seek(offset)
    data = stream.read(length)
    if len(data) != length:
        raise FormatError("file changed or truncated during inspection")
    return data


def inspect_image(path):
    """Report outer metadata only. Never guess a raw image's ISA or load address."""
    path = Path(path)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        size = 0
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
        head = read_at(stream, 0, min(size, 64), size)
        report = {"name": path.name, "size_bytes": size,
                  "sha256": digest.hexdigest(), "format": "unknown"}
        if head.startswith(b"\x7fELF"):
            if len(head) < 16 or head[4] not in (1, 2) or head[5] not in (1, 2) or head[6] != 1:
                raise FormatError("invalid ELF identification")
            bits = 32 if head[4] == 1 else 64
            header = read_at(stream, 0, 52 if bits == 32 else 64, size)
            endian = "<" if head[5] == 1 else ">"
            machine = struct.unpack_from(endian + "H", header, 18)[0]
            version = struct.unpack_from(endian + "I", header, 20)[0]
            header_size = struct.unpack_from(endian + "H", header, 40 if bits == 32 else 52)[0]
            if version != 1 or header_size != len(header):
                raise FormatError("invalid ELF header version or size")
            entry = struct.unpack_from(endian + ("I" if bits == 32 else "Q"), header, 24)[0]
            report.update(format="elf", bits=bits,
                          byte_order="little" if endian == "<" else "big",
                          machine_id=machine, architecture=ELF_MACHINES.get(machine, "unknown"),
                          entry_point=hex(entry))
        elif head.startswith(b"MZ"):
            dos = read_at(stream, 0, 64, size)
            offset = struct.unpack_from("<I", dos, 60)[0]
            if offset < 64:
                raise FormatError("invalid PE header offset")
            coff = read_at(stream, offset, 24, size)
            if coff[:4] != b"PE\0\0":
                raise FormatError("MZ file has no PE/COFF signature at declared offset")
            machine, sections = struct.unpack_from("<HH", coff, 4)
            optional_size = struct.unpack_from("<H", coff, 20)[0]
            read_at(stream, offset + 24, optional_size + sections * 40, size)
            report.update(format="pe-coff", machine_id=machine,
                          architecture=PE_MACHINES.get(machine, "unknown"), sections=sections)
        elif head[:16] == FMP_GUID.bytes_le:
            header = read_at(stream, 0, 28, size)
            header_size, flags, image_size = struct.unpack_from("<III", header, 16)
            if not 28 <= header_size <= image_size or image_size != size:
                raise FormatError("invalid FMP capsule header/image size")
            report.update(format="uefi-fmp-capsule", capsule_guid=str(FMP_GUID),
                          header_size=header_size, flags=hex(flags),
                          payload_size_bytes=image_size - header_size)
        elif head.startswith(b"MSCF"):
            header = read_at(stream, 0, 36, size)
            if struct.unpack_from("<I", header, 8)[0] != size:
                raise FormatError("CAB declared size does not match file size")
            report.update(format="microsoft-cabinet")
    return report


def qemu_smoke(args):
    """Run generic EDK II on virt. Never claim this is DGX Spark emulation."""
    qemu = shutil.which(args.qemu)
    if qemu is None:
        raise ValueError(f"QEMU executable not found: {args.qemu}")
    firmware = args.firmware.resolve(strict=True)
    if not firmware.is_file():
        raise ValueError("firmware must be a regular file")
    if args.timeout <= 0:
        raise ValueError("timeout must be positive")
    version = subprocess.check_output([qemu, "--version"], text=True).splitlines()[0]
    command = [qemu, "-machine", "virt,gic-version=3", "-accel", "tcg",
               "-cpu", "cortex-a72", "-smp", "1", "-m", "512M", "-nodefaults",
               "-display", "none", "-monitor", "none", "-serial", "stdio",
               "-nic", "none", "-no-reboot", "-bios", str(firmware)]
    timed_out = False
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=args.timeout, check=False)
        output, returncode = result.stdout, result.returncode
    except subprocess.TimeoutExpired as error:
        # subprocess.run kills and reaps the child before propagating the timeout.
        output, returncode, timed_out = error.output or b"", None, True
    passed = b"UEFI Interactive Shell" in output and (timed_out or returncode == 0)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    log = args.output_dir / "qemu-uefi.log"
    log.write_bytes(output)
    metadata = {"baseline_only": True, "spark_firmware_executed": False,
                "passed": passed, "qemu_version": version, "command": command,
                "firmware": inspect_image(firmware), "timeout_seconds": args.timeout,
                "stopped_at_timeout": timed_out, "returncode": returncode}
    (args.output_dir / "qemu-uefi.json").write_text(json.dumps(metadata, indent=2) + "\n")
    status = "PASS" if passed else "FAIL"
    print(f"{status}: generic QEMU/EDK II UEFI shell baseline; Spark firmware not executed")
    print(f"Serial log: {log}")
    if not passed:
        print("Expected UEFI Interactive Shell marker was absent or QEMU failed.", file=sys.stderr)
    return 0 if passed else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="hash and inspect an artifact's outer header")
    inspect.add_argument("image", type=Path)
    smoke = commands.add_parser("qemu-smoke", help="generic UEFI baseline, not Spark emulation")
    smoke.add_argument("--firmware", type=Path, required=True,
                       help="generic QEMU AArch64 EDK II image containing a UEFI shell")
    smoke.add_argument("--qemu", default="qemu-system-aarch64")
    smoke.add_argument("--timeout", type=int, default=20)
    smoke.add_argument("--output-dir", type=Path, default=Path("build"))
    args = parser.parse_args(argv)
    try:
        if args.command == "inspect":
            print(json.dumps(inspect_image(args.image), indent=2))
            return 0
        return qemu_smoke(args)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
