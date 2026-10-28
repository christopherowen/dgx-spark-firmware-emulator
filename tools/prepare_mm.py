#!/usr/bin/env python3
"""Extract the unchanged pinned Standalone MM package into ignored firmware/."""

import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.download_firmware import require_ignored_untracked
from tools.spark_image import DIRECTORY, capsule_payload, checked, prepare, sha256

MM_SHA256 = "591d5b33bad81bed6ec402dd2a5d20ed37bd3920d261420e0c4e3ba09d9431fa"


def unpack_mm(payload, expected_hash=MM_SHA256):
    candidates = []
    offset = 0
    while (offset := payload.find(b"SPKG", offset)) >= 0:
        try:
            magic, version, manifest, manifest_size, image, image_size, r0, r1 = struct.unpack(
                "<8I", checked(payload, offset, 32))
            if (version == 2 and not (r0 or r1) and manifest >= 32 and
                    manifest_size >= 40 and image >= manifest + manifest_size and image_size >= 56):
                package = checked(payload, offset, image + image_size)
                fdt = checked(package, manifest, manifest_size)
                if (struct.unpack_from(">II", fdt) == (0xd00dfeed, manifest_size) and
                        checked(package, image + 40, 4) == b"_FVH"):
                    candidates.append(package)
        except (ValueError, struct.error):
            pass
        offset += 4
    if len(candidates) != 1:
        raise ValueError(f"expected one valid SPKG v2 MM package; found {len(candidates)}")
    package = candidates[0]
    if sha256(package) != expected_hash:
        raise ValueError("Standalone MM package checksum mismatch")
    return package


def prepare_mm():
    prepare()  # Validates the complete pinned cabinet and capsule first.
    package = unpack_mm(capsule_payload((DIRECTORY / "socfw.cap").read_bytes()))
    path = DIRECTORY / "stmm.pkg"
    if path.is_symlink():
        raise ValueError("MM output must not be a symbolic link")
    require_ignored_untracked(ROOT, path)
    path.write_bytes(package)
    return path


if __name__ == "__main__":
    path = prepare_mm()
    print(json.dumps({"path": str(path), "sha256": MM_SHA256,
                      "load_address": "0x93a40000", "signature_verified": False}, indent=2))
