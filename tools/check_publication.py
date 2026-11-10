#!/usr/bin/env python3
"""Reject tracked local firmware inputs, outputs, and common firmware containers.

It also enforces one owner rule mechanically: **flash-readback material does not belong in this
repository.** That rule was broken once — a readback payload and its tests were committed here and had to
be purged from history — so it is checked rather than remembered. A file whose name marks it as readback
work, or whose text carries the marker that only that work uses, fails the check.
"""

from pathlib import Path, PurePosixPath
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
FIRMWARE_SUFFIXES = {".cap", ".cab", ".bin", ".fd", ".rom", ".elf", ".efi", ".dtb", ".pkg"}
FIRMWARE_PREFIXES = (b"\x7fELF", b"MSCF", b"MZ", b"\xd0\x0d\xfe\xed",
                     b"SPKG", b"SF_BOOT", b"MSS1",
                     bytes.fromhex("edd5cb6d2de8444cbda17194199ad92a"))
# Readback of a unit's NOR part is local-only by owner rule; these markers name that work.
READBACK_NAME_MARKERS = ("nor_dump", "nor_readback", "readback_payload", "prearm_dump")
READBACK_TEXT_MARKERS = (b"SPARK_NOR_PREARM_DUMP", b"--monitor-flag prearm")
# The rule's own implementation and its test have to name the markers; they are not readback work.
READBACK_TEXT_EXEMPT = {"tools/check_publication.py", "tests/test_publication_rule.py"}
# Names and paths that belong to the private lab, not to a public tree. The patterns are assembled from
# fragments so this file does not itself contain the strings it rejects.
HOST_NAME_MARKERS = (b"spark-" + b"0", b"spark-" + b"00", b"dgx" + b"4", b"sparks_" + b"config",
                     b"dgx-" + b"jailbreak", b"/home/" + b"masterchef")


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
            continue
        lowered = name.lower()
        if any(marker in lowered for marker in READBACK_NAME_MARKERS):
            rejected.append(name)
            continue
        if relative.suffix.lower() in {".c", ".h", ".py", ".md"} and name not in READBACK_TEXT_EXEMPT:
            with subprocess.Popen(["git", "-C", str(root), "show", ":" + name],
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
                body = process.stdout.read(1 << 20)
                process.wait()
            if any(marker in body for marker in READBACK_TEXT_MARKERS + HOST_NAME_MARKERS):
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
