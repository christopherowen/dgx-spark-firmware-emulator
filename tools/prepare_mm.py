#!/usr/bin/env python3
"""Extract the unchanged pinned Standalone MM package into ignored firmware/."""

import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.download_firmware import require_ignored_untracked
from tools.spark_image import (DEFAULT_VERSION, capsule_payload, checked, prepare,
                               prepared_directory, sha256)

MM_SHA256 = {
    "2.158.15": "591d5b33bad81bed6ec402dd2a5d20ed37bd3920d261420e0c4e3ba09d9431fa",
    "2.155.11": "ae81c97a13fb31222b67f6391a1c4145d25c4ec433896a5d21a6b4a28533f90d",
    "4.1.0": "d23fff1019735b961ef84686afaa6dc75cd259cc2f7d34659077f84890cc8c8c",
}


def unpack_mm(payload, expected_hash=MM_SHA256[DEFAULT_VERSION]):
    """expected_hash=None reports the package without a pinned comparison."""
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
    if expected_hash is not None and sha256(package) != expected_hash:
        raise ValueError("Standalone MM package checksum mismatch")
    return package


def prepare_mm(version=DEFAULT_VERSION):
    prepare(version)  # Validates the complete pinned cabinet and capsule first.
    directory = prepared_directory(version)
    expected = MM_SHA256.get(version)
    if expected is None:
        raise ValueError(f"no pinned Standalone MM hash for {version}")
    package = unpack_mm(capsule_payload((directory / "socfw.cap").read_bytes()), expected)
    path = directory / "stmm.pkg"
    if path.is_symlink():
        raise ValueError("MM output must not be a symbolic link")
    require_ignored_untracked(ROOT, path)
    path.write_bytes(package)
    return path


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="extract the pinned Standalone MM package")
    parser.add_argument("--version", default=DEFAULT_VERSION, choices=sorted(MM_SHA256))
    arguments = parser.parse_args()
    path = prepare_mm(arguments.version)
    print(json.dumps({"path": str(path), "sha256": MM_SHA256[arguments.version],
                      "load_address": "0x93a40000", "signature_verified": False}, indent=2))
