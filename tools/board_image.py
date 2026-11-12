#!/usr/bin/env python3
"""Compose and split the board's 64 MiB flash image.

The measured layout, from the emulator's own NOR dumps and the prepared images:

    0x00000000 .. 0x02000000   variable-store FV (FV1): FV header, NVAR records
                               from 0x60, free space to 0x3fe80
    0x02000000 .. 0x04000000   the code FV: exactly the bytes of `uefi.fd`
    0x04000000                 end of the device

The model used to carry only the store, at an invented offset, with the code loaded
from a host file. This tool builds the one image both halves really share, so a write
to the code region is visible to the next boot.

usage:
    board_image.py compose --store firmware/state/store.bin --code firmware/prepared/X/uefi.fd --out image.bin
    board_image.py split image.bin --store store.bin --code code.bin
    board_image.py inspect image.bin
"""
from __future__ import annotations

import argparse
import hashlib
from dataclasses import dataclass
from pathlib import Path
import sys

DEVICE_SIZE = 0x4000000        # the board's part is 64 MiB
STORE_OFFSET = 0x0000000       # FV1: the writable, memory-mapped variable volume
STORE_SIZE = 0x2000000
CODE_OFFSET = 0x02000000       # the code FV, byte-identical to uefi.fd
CODE_SIZE = 0x2000000
FV_SIGNATURE = b"_FVH"
FV_SIGNATURE_OFFSET = 0x28


class ImageError(ValueError):
    """The inputs cannot form a board image."""


@dataclass(frozen=True)
class BoardImage:
    store: bytes
    code: bytes

    @property
    def size(self) -> int:
        return DEVICE_SIZE

    def compose(self) -> bytes:
        if len(self.store) != STORE_SIZE:
            raise ImageError(f"store must be {STORE_SIZE:#x} bytes, not {len(self.store):#x}")
        if len(self.code) != CODE_SIZE:
            raise ImageError(f"code must be {CODE_SIZE:#x} bytes, not {len(self.code):#x}")
        for name, blob, offset in (("store", self.store, STORE_OFFSET),
                                   ("code", self.code, CODE_OFFSET)):
            if blob[FV_SIGNATURE_OFFSET:FV_SIGNATURE_OFFSET + 4] != FV_SIGNATURE:
                raise ImageError(f"{name} does not start with a firmware volume at {offset:#x}")
        image = bytearray(b"\xff" * DEVICE_SIZE)
        image[STORE_OFFSET:STORE_OFFSET + STORE_SIZE] = self.store
        image[CODE_OFFSET:CODE_OFFSET + CODE_SIZE] = self.code
        return bytes(image)


def erased_store(size: int = STORE_SIZE) -> bytes:
    """An erased store half in the shape the firmware's own volume uses.

    Measured on a readback: erased, with the volume length at `0x20` and `_FVH` at
    `0x28`; everything else in the header is `0xff`. Building anything stricter would
    produce an image the real part never carries.
    """
    if size != STORE_SIZE:
        raise ImageError(f"a store half is {STORE_SIZE:#x} bytes, not {size:#x}")
    store = bytearray(b"\xff" * size)
    store[0x20:0x28] = (0x40000).to_bytes(8, "little")
    store[FV_SIGNATURE_OFFSET:FV_SIGNATURE_OFFSET + 4] = FV_SIGNATURE
    return bytes(store)


def fresh_image(code: bytes, store: bytes | None = None) -> bytes:
    """A device image that can boot from erased: erased store, the release's code volume."""
    padded = code + b"\xff" * (CODE_SIZE - len(code))
    return BoardImage(store=store if store is not None else erased_store(),
                      code=padded).compose()


def split(image: bytes) -> BoardImage:
    if len(image) != DEVICE_SIZE:
        raise ImageError(f"board image must be {DEVICE_SIZE:#x} bytes, not {len(image):#x}")
    return BoardImage(store=image[STORE_OFFSET:STORE_OFFSET + STORE_SIZE],
                      code=image[CODE_OFFSET:CODE_OFFSET + CODE_SIZE])


def report(image: bytes) -> dict:
    parts = split(image)
    return {
        "size": len(image),
        "sha256": hashlib.sha256(image).hexdigest(),
        "store": {"offset": STORE_OFFSET, "size": len(parts.store),
                  "sha256": hashlib.sha256(parts.store).hexdigest(),
                  "fv": parts.store[FV_SIGNATURE_OFFSET:FV_SIGNATURE_OFFSET + 4] == FV_SIGNATURE},
        "code": {"offset": CODE_OFFSET, "size": len(parts.code),
                 "sha256": hashlib.sha256(parts.code).hexdigest(),
                 "fv": parts.code[FV_SIGNATURE_OFFSET:FV_SIGNATURE_OFFSET + 4] == FV_SIGNATURE},
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    compose_cmd = sub.add_parser("compose", help="build the board image from its two halves")
    compose_cmd.add_argument("--store", type=Path, required=True)
    compose_cmd.add_argument("--code", type=Path, required=True)
    compose_cmd.add_argument("--erased-store", action="store_true",
                             help="build the store half erased, for a fresh device image")
    compose_cmd.add_argument("--out", type=Path, required=True)
    split_cmd = sub.add_parser("split", help="extract the two halves")
    split_cmd.add_argument("image", type=Path)
    split_cmd.add_argument("--store", type=Path, required=True)
    split_cmd.add_argument("--code", type=Path, required=True)
    inspect_cmd = sub.add_parser("inspect", help="report the layout and hashes")
    inspect_cmd.add_argument("image", type=Path)
    args = parser.parse_args(argv)

    try:
        if args.command == "compose":
            store = erased_store() if args.erased_store else args.store.read_bytes()
            parts = BoardImage(store=store, code=args.code.read_bytes())
            image = parts.compose()
            if args.out.exists():
                raise ImageError(f"refusing to overwrite {args.out}")
            args.out.parent.mkdir(parents=True, exist_ok=True)
            with args.out.open("xb") as handle:
                handle.write(image)
            print(f"composed {args.out} ({len(image):#x} bytes, "
                  f"{hashlib.sha256(image).hexdigest()[:16]})")
        elif args.command == "split":
            parts = split(args.image.read_bytes())
            for path, blob in ((args.store, parts.store), (args.code, parts.code)):
                if path.exists():
                    raise ImageError(f"refusing to overwrite {path}")
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("xb") as handle:
                    handle.write(blob)
                print(f"wrote {path} ({len(blob):#x} bytes)")
        else:
            info = report(args.image.read_bytes())
            print(f"size   {info['size']:#x}")
            print(f"sha256 {info['sha256']}")
            for half in ("store", "code"):
                part = info[half]
                print(f"{half:6} {part['offset']:#010x}..{part['offset'] + part['size']:#010x}"
                      f"  sha256 {part['sha256'][:16]}  firmware volume {part['fv']}")
    except (ImageError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
