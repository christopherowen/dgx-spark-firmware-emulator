#!/usr/bin/env python3
"""Read a file out of a disk image's FAT partition without mounting anything.

The installed Spark disk is read from inside the emulator, but some artefacts have to reach the host
to be useful — a symbol table, for instance, which turns a sampled guest address into a function name.
No tool on this machine reads a filesystem at a partition offset, and mounting needs privileges, so
this walks the partition table itself and then the FAT directory entries.

Supports what an EFI System Partition actually is: a GPT or MBR partition table, FAT32, and long file
names (so `SYSMAP.TXT` or `system.map-7.0.0-1019-nvidia` both resolve).
"""

from __future__ import annotations

import argparse
import pathlib
import struct
import sys


def partitions(image: pathlib.Path) -> list[tuple[int, int, int]]:
    """Return (index, byte offset, byte length) for each partition, in table order."""
    with image.open("rb") as handle:
        handle.seek(512)
        gpt = handle.read(8) == b"EFI PART"
        found: list[tuple[int, int, int]] = []
        if gpt:
            handle.seek(1024)  # LBA 2: first partition entry
            for index in range(128):
                entry = handle.read(128)
                if entry[:16] == b"\0" * 16:
                    break
                first, last = struct.unpack_from("<QQ", entry, 32)
                if last >= first:
                    found.append((index + 1, first * 512, (last - first + 1) * 512))
            return found
        handle.seek(446)
        for index in range(4):
            entry = handle.read(16)
            first, count = struct.unpack_from("<II", entry, 8)
            if first and count:
                found.append((index + 1, first * 512, count * 512))
    return found


class Fat:
    def __init__(self, handle, offset: int, length: int):
        self.handle = handle
        handle.seek(offset)
        head = handle.read(512)
        if head[510:512] != b"\x55\xaa":
            raise SystemExit("error: the partition does not look like FAT")
        self.base = offset
        self.bps = struct.unpack_from("<H", head, 11)[0]
        self.spc = head[13]
        self.reserved = struct.unpack_from("<H", head, 14)[0]
        self.fats = head[16]
        self.fat_sectors = struct.unpack_from("<I", head, 36)[0] or struct.unpack_from("<H", head, 22)[0]
        root = struct.unpack_from("<I", head, 44)[0]
        if self.fat_sectors == 0:
            raise SystemExit("error: FAT12/16 is not supported here")
        self.root_cluster = root
        self.cluster_bytes = self.bps * self.spc
        self.data_start = (self.reserved + self.fats * self.fat_sectors) * self.bps
        self.fat_start = self.reserved * self.bps

    def _read(self, offset: int, count: int) -> bytes:
        self.handle.seek(self.base + offset)
        return self.handle.read(count)

    def chain(self, cluster: int) -> list[int]:
        chain, seen = [], set()
        while 2 <= cluster < 0x0FFFFFF8:
            if cluster in seen:
                raise SystemExit("error: FAT chain loops")
            seen.add(cluster)
            chain.append(cluster)
            entry = struct.unpack("<I", self._read(self.fat_start + cluster * 4, 4))[0] & 0x0FFFFFFF
            cluster = entry
        return chain

    def entries(self, cluster: int):
        for start in self.chain(cluster):
            data = self._read(self.data_start + (start - 2) * self.cluster_bytes, self.cluster_bytes)
            long_name = ""
            for position in range(0, len(data), 32):
                entry = data[position:position + 32]
                if entry[0] == 0:
                    return
                if entry[0] == 0xE5:
                    long_name = ""
                    continue
                attributes = entry[11]
                if attributes == 0x0F:
                    # Long-name entries arrive in reverse order, and the last fragment is flagged.
                    chunk = (entry[1:11] + entry[14:26] + entry[28:32]).decode("utf-16-le", "replace")
                    # The final fragment ends with a 0x0000 terminator and the rest padded with
                    # 0xffff; either one ends the name.
                    for terminator in ("\x00", "\uffff"):
                        chunk = chunk.split(terminator)[0]
                    long_name = chunk + long_name
                    continue
                if attributes & 0x08:
                    # The volume label is a root-directory entry that carries the filesystem's own
                    # name, so `mkfs.vfat -n EFI` makes a label that matches the EFI directory.
                    long_name = ""
                    continue
                if attributes & 0x10 and entry[0] == 0x2E:
                    long_name = ""
                    continue
                # An 8.3 entry has no dot: the name is eight bytes and the extension three, both
                # space-padded. Splitting on a dot leaves "SYSMAP  TXT" unmatched forever.
                stem = entry[0:8].decode("ascii", "replace").rstrip()
                extension = entry[8:11].decode("ascii", "replace").rstrip()
                short = stem + ("." + extension if extension else "")
                high, low = struct.unpack_from("<H", entry, 20)[0], struct.unpack_from("<H", entry, 26)[0]
                yield (long_name or short, attributes, (high << 16) | low,
                       struct.unpack_from("<I", entry, 28)[0])
                long_name = ""

    def find(self, path: str):
        cluster = self.root_cluster
        target = None
        parts = [part for part in path.replace("\\", "/").split("/") if part]
        for index, part in enumerate(parts):
            for name, attributes, first, size in self.entries(cluster):
                if name.lower() != part.lower():
                    continue
                if index == len(parts) - 1:
                    if attributes & 0x10:
                        raise SystemExit(f"error: {path} is a directory")
                    target = (first, size)
                elif attributes & 0x10:
                    cluster = first
                else:
                    continue
                break
            if target is None and index == len(parts) - 1:
                raise SystemExit(f"error: {path} not found in the image")
        return target

    def read_file(self, path: str) -> bytes:
        first, size = self.find(path)
        data = bytearray()
        for cluster in self.chain(first):
            data += self._read(self.data_start + (cluster - 2) * self.cluster_bytes, self.cluster_bytes)
        return bytes(data[:size])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--image", type=pathlib.Path, required=True)
    parser.add_argument("--partition", type=int, default=1,
                        help="partition number in table order (default 1)")
    parser.add_argument("--file", help="path inside the partition, e.g. SYSMAP.TXT")
    parser.add_argument("--output", type=pathlib.Path, help="write here instead of standard output")
    parser.add_argument("--list", action="store_true", help="list the partition root instead")
    args = parser.parse_args()
    if not args.list and not args.file:
        parser.error("--file is required unless --list is given")
    table = partitions(args.image)
    match = [entry for entry in table if entry[0] == args.partition]
    if not match:
        raise SystemExit(f"error: partition {args.partition} not found; table has "
                         f"{[entry[0] for entry in table]}")
    _, offset, length = match[0]
    with args.image.open("rb") as handle:
        fat = Fat(handle, offset, length)
        if args.list:
            for name, attributes, _, size in fat.entries(fat.root_cluster):
                print(f"{'dir ' if attributes & 0x10 else size:>10}  {name}")
            return 0
        data = fat.read_file(args.file)
    if args.output:
        args.output.write_bytes(data)
        print(f"{args.output}: {len(data)} bytes from {args.file}")
    else:
        sys.stdout.buffer.write(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
