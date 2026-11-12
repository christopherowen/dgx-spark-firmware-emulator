#!/usr/bin/env python3
"""Turn sampled guest addresses into function names.

`--trace-sample N` answers "where is the guest running" for a boot that stops printing, but it answers
in addresses. This reads those samples out of a run's `qemu.log` and resolves each one against the
kernel's `System.map`, printing the symbols ranked by how often they were sampled — which is what a
stall looks like: one function at the top, usually with an offset that moves.

Use `system.map-<version>` from the same kernel that booted, and boot with `nokaslr` so the addresses
in the map are the addresses in the samples.
"""

from __future__ import annotations

import argparse
import collections
import pathlib
import re
import sys

SAMPLE = re.compile(r"pc=(0x[0-9a-fA-F]+)")
SYMBOL = re.compile(r"^([0-9a-fA-F]{8,16})\s+([A-Za-z])\s+(\S+)")


def load_map(path: pathlib.Path) -> list[tuple[int, str]]:
    symbols = []
    for line in path.read_text(errors="replace").splitlines():
        match = SYMBOL.match(line)
        if match:
            symbols.append((int(match.group(1), 16), match.group(3)))
    if not symbols:
        raise SystemExit(f"error: no symbols in {path}; is this a System.map?")
    symbols.sort()
    return symbols


def resolve(symbols: list[tuple[int, str]], address: int) -> tuple[str, int]:
    import bisect
    index = bisect.bisect_right(symbols, (address, "\uffff")) - 1
    if index < 0:
        return ("?", address)
    base, name = symbols[index]
    return (name, address - base)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--log", type=pathlib.Path, action="append", required=True,
                        help="a run's qemu.log (repeatable)")
    parser.add_argument("--map", type=pathlib.Path, required=True,
                        help="the System.map of the kernel that booted")
    parser.add_argument("--top", type=int, default=15)
    parser.add_argument("--addresses", action="store_true",
                        help="also list the distinct addresses per symbol, with offsets")
    args = parser.parse_args()

    symbols = load_map(args.map)
    counts: collections.Counter = collections.Counter()
    offsets: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    total = 0
    for path in args.log:
        for line in path.read_text(errors="replace").splitlines():
            match = SAMPLE.search(line)
            if not match:
                continue
            address = int(match.group(1), 16)
            name, offset = resolve(symbols, address)
            counts[name] += 1
            offsets[name][offset] += 1
            total += 1
    if not total:
        raise SystemExit("error: no SAMPLE lines; did the run use --trace-sample?")
    print(f"{total} samples, {len(counts)} symbols")
    for name, count in counts.most_common(args.top):
        share = 100.0 * count / total
        print(f"{count:8d}  {share:5.1f}%  {name}")
        if args.addresses:
            for offset, hits in offsets[name].most_common(5):
                print(f"          +{offset:#x}  ×{hits}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
