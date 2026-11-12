#!/usr/bin/env python3
"""Build the recovery media the installer's own mount step accepts.

The recovery initramfs (`init_mount`) mounts a media device, requires
`fastos-release.txt` at its root, and later reads `efi.tar.xz`,
`fastos.partaa` and `fastos.partab` from the same mount. On hardware that
media is a USB key; in this emulator the key is attached as a virtio-scsi
disk, because the recovery initramfs modprobes `xhci-plat-hcd` and this
machine describes no platform USB host for it to bind.

Two properties of `init_mount` decide the image's shape, and both are the
installer's own, not ours:

  * it only looks at `/dev/sd??` (a partition) or `/dev/sd?` as ISO, so the
    image needs a partition table and a partition;
  * it mounts with no `-t`, so the partition's filesystem can be any type the
    guest can autodetect. This builds ext4, which `debugfs` can populate
    without a mount or root.

The payload is copied out of the vendor archive, never re-encoded, and each
member's size and sha256 are recorded next to the image so a later run can
prove it used the same media.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess
import sys
import tarfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
MEMBERS = (
    "usbimg.customer/usb/fastos-release.txt",
    "usbimg.customer/usb/efi.tar.xz",
    "usbimg.customer/usb/fastos.partaa",
    "usbimg.customer/usb/fastos.partab",
)
PARTITION_START = 2048  # sectors; 1 MiB, the same alignment init_mount's own sgdisk uses
SUFFIXES = {"": 1, "K": 1 << 10, "M": 1 << 20, "G": 1 << 30, "T": 1 << 40}


def parse_size(text: str) -> int:
    """A size with an optional binary suffix: '7680M' is 7680 MiB, never 7680 MB."""
    body = text.strip().upper().rstrip("IB")
    number, suffix = body[:-1], body[-1:] if body[-1:] in SUFFIXES else ""
    if suffix:
        body = body[:-1]
    try:
        value = int(body)
    except ValueError:
        raise SystemExit(f"error: cannot read a size from {text!r}") from None
    return value * SUFFIXES[suffix]


def extract(archive: pathlib.Path, staging: pathlib.Path) -> list[dict]:
    staging.mkdir(parents=True, exist_ok=True)
    entries = []
    with tarfile.open(archive) as tar:
        for member in MEMBERS:
            handle = tar.extractfile(member)
            if handle is None:
                raise SystemExit(f"error: {member} is not a regular file in {archive}")
            target = staging / member.rsplit("/", 1)[-1]
            digest = hashlib.sha256()
            written = 0
            with target.open("wb") as out:
                while True:
                    chunk = handle.read(1 << 24)
                    if not chunk:
                        break
                    digest.update(chunk)
                    out.write(chunk)
                    written += len(chunk)
            entries.append({"member": member, "name": target.name, "size": written,
                            "sha256": digest.hexdigest()})
            print(f"  {target.name:20} {written:>12} bytes")
    return entries


def build(partition: pathlib.Path, staging: pathlib.Path, entries: list[dict],
          output: pathlib.Path) -> None:
    subprocess.run(["mkfs.ext4", "-q", "-F", "-L", "SPARKKEY", "-m", "0", str(partition)],
                   check=True)
    for entry in entries:
        subprocess.run(["debugfs", "-w", "-R",
                        f"write {staging / entry['name']} /{entry['name']}",
                        str(partition)], check=True, capture_output=True)
    sectors = partition.stat().st_size // 512
    mbr = bytearray(512)
    # One primary partition, type 0x83 (Linux), start and end at sector granularity.
    mbr[446:462] = bytes([0x00, 0xFE, 0xFF, 0xFF, 0x83, 0xFE, 0xFF, 0xFF]) + \
        PARTITION_START.to_bytes(4, "little") + sectors.to_bytes(4, "little")
    mbr[510:512] = b"\x55\xaa"
    with output.open("wb") as out:
        out.write(bytes(mbr))
        out.write(b"\0" * (PARTITION_START * 512 - 512))
        with partition.open("rb") as source:
            while True:
                chunk = source.read(1 << 24)
                if not chunk:
                    break
                out.write(chunk)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--recovery", type=pathlib.Path, required=True,
                        help="the vendor recovery archive")
    parser.add_argument("--output", type=pathlib.Path, required=True,
                        help="the media image; its partition carries the payload")
    parser.add_argument("--partition-size", default="7680M",
                        help="size of the ext4 partition inside the image (default 7680M)")
    args = parser.parse_args()
    staging = args.output.with_suffix(args.output.suffix + ".staging")
    partition = args.output.with_suffix(args.output.suffix + ".part")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    print(f"extracting {args.recovery}")
    entries = extract(args.recovery, staging)
    needed = sum(e["size"] for e in entries)
    if partition.exists() and partition.stat().st_size < needed * 1.1:
        partition.unlink()
    if not partition.exists():
        with partition.open("wb") as handle:
            handle.truncate(parse_size(args.partition_size))
    print(f"building {args.output} (partition {partition.stat().st_size} bytes, "
          f"{needed} bytes of payload)")
    build(partition, staging, entries, args.output)
    record = {"recovery": str(args.recovery), "output": str(args.output),
              "partition_bytes": partition.stat().st_size,
              "image_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
              "members": entries}
    args.output.with_suffix(".json").write_text(json.dumps(record, indent=2) + "\n")
    for entry in entries:
        path = staging / entry["name"]
        if path.exists():
            path.unlink()
    print(f"{args.output} ready: {args.output.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
