#!/usr/bin/env python3
"""Reject tracked local firmware inputs, outputs, and common firmware containers.

It also rejects owner-specific markers: private host names, lab paths and the names of work that must
stay local. Those markers are deliberately not in this file. They are read from the ignored
`firmware/publication-markers.txt` (one marker per line, `#` starts a comment) and matched,
case-insensitively, against every tracked path and the text of every tracked text file. Without that
file only the firmware checks run, which is what CI does.
"""

from pathlib import Path, PurePosixPath
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
FIRMWARE_SUFFIXES = {".cap", ".cab", ".bin", ".fd", ".rom", ".elf", ".efi", ".dtb", ".pkg"}
FIRMWARE_PREFIXES = (b"\x7fELF", b"MSCF", b"MZ", b"\xd0\x0d\xfe\xed",
                     b"SPKG", b"SF_BOOT", b"MSS1",
                     bytes.fromhex("edd5cb6d2de8444cbda17194199ad92a"))
MARKERS_FILE = PurePosixPath("firmware/publication-markers.txt")


def markers(root=REPO_ROOT):
    """The local, ignored marker list; empty when the file is absent."""
    path = Path(root) / MARKERS_FILE
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return ()
    found = []
    for line in lines:
        marker = line.split("#", 1)[0].strip()
        if marker:
            found.append(marker.lower().encode())
    return tuple(found)


def _staged(root, name):
    # Inspect the staged blob, including force-added files that .gitignore cannot hide.
    # Missing/deleted working-tree copies must not let an index entry evade the check.
    result = subprocess.run(["git", "-C", str(root), "show", ":" + name],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if result.returncode != 0:
        raise RuntimeError(f"could not inspect staged file: {name}")
    return result.stdout


def violations(root=REPO_ROOT):
    root = Path(root)
    owner_markers = markers(root)
    names = subprocess.check_output(["git", "-C", str(root), "ls-files", "-z"])
    rejected = []
    for name in filter(None, names.decode().split("\0")):
        relative = PurePosixPath(name)
        forbidden_directory = relative.parts[0] in {"firmware", "captures", "build"}
        if (forbidden_directory and name != "firmware/README.md") or relative.suffix.lower() in FIRMWARE_SUFFIXES:
            rejected.append(name)
            continue
        data = _staged(root, name)
        if data.startswith(FIRMWARE_PREFIXES):
            rejected.append(name)
            continue
        if owner_markers:
            lowered_name = name.lower().encode()
            text = b"\0" not in data[:8192]
            body = data.lower() if text else b""
            if any(marker in lowered_name or marker in body for marker in owner_markers):
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
