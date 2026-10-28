#!/usr/bin/env python3
"""Reject tracked local firmware inputs, outputs, and common firmware containers."""

from pathlib import Path, PurePosixPath
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
FIRMWARE_SUFFIXES = {".cap", ".cab", ".bin", ".fd", ".rom", ".elf", ".efi", ".dtb", ".pkg"}
FIRMWARE_PREFIXES = (b"\x7fELF", b"MSCF", b"MZ", b"\xd0\x0d\xfe\xed",
                     b"SPKG", b"SF_BOOT", b"MSS1",
                     bytes.fromhex("edd5cb6d2de8444cbda17194199ad92a"))


def violations(root=REPO_ROOT):
    root = Path(root)
    names = subprocess.check_output(["git", "-C", str(root), "ls-files", "-z"])
    rejected = []
    for name in filter(None, names.decode().split("\0")):
        relative = PurePosixPath(name)
        forbidden_directory = relative.parts[0] in {"firmware", "captures", "build"}
        if (forbidden_directory and name != "firmware/README.md") or relative.suffix.lower() in FIRMWARE_SUFFIXES:
            rejected.append(name)
            continue
        # Inspect the staged blob, including force-added files that .gitignore cannot hide.
        # Missing/deleted working-tree copies must not let an index entry evade the check.
        with subprocess.Popen(["git", "-C", str(root), "show", ":" + name],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
            prefix = process.stdout.read(16)
            # Drain the pipe so git can exit normally, even for a large staged file.
            while process.stdout.read(1024 * 1024):
                pass
            code = process.wait()
        if code != 0:
            raise RuntimeError(f"could not inspect staged file: {name}")
        if prefix.startswith(FIRMWARE_PREFIXES):
            rejected.append(name)
    return rejected


def main():
    try:
        rejected = violations()
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    if rejected:
        print("Firmware/local artifacts must not be committed:", file=sys.stderr)
        for name in rejected:
            print(f"  {name}", file=sys.stderr)
        return 1
    print("Publication check passed: no tracked firmware/local artifacts detected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
