#!/usr/bin/env python3
"""Prepare the pinned Spark SoC UEFI image locally without altering its bytes."""

import hashlib
import json
import lzma
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.download_firmware import download, require_ignored_untracked

# Every release we can prepare, keyed by SoC version. `capsule` and `uefi` are
# None until first extracted; fill them in from the printed hashes so an image is
# always checked against a pinned value afterwards.
VERSIONS = {
    "2.158.15": {                                   # LVFS 152274, our reproduction build
        "cab": "f895952896ffed39fc167de0f86eab40609b5baf9debcf12caa720123a915416",
        "capsule": "00393be197569851c9a72bd214cf22f45a04b7e1fc759ab4ccc68c98dd650d78",
        "uefi": "568ff181d47fbfd31504a22d6e42975db77b99b02c07fe5c76823b484441e645",
    },
    "4.1.0": {                                      # newest archived release, combined image
        "cab": "9e48bcdd45f53d98e9ad52a3ee86ea769fc499467ebcb3f592911c0d09d0e050",
        "capsule": "87a26583741422bdcc4f7d0cb9305bbe3bba0408bbc573cd80949e688fc3b5b9",
        "uefi": "e8dc43f51d0eecac1717dd7cef53f29bbffe6a92c0737aff094dbed5df131999",
    },
    "2.155.11": {                                   # LVFS 143466, the release the board was running
        "cab": "35e2452645affc35365ee66259d82908d441283f61a16b8305593bcff3e505b1",
        "capsule": "0985b848b1708421a399935b7f9b1afb5469588db6f26bd2953c6013f3db70ff",
        "uefi": "5ee97b880e8bda20ab3cba0fc60eb747225f17a55a699029141207f573963733",
    },
}
DEFAULT_VERSION = "2.158.15"
VERSION = DEFAULT_VERSION
CAB_SHA256 = VERSIONS[VERSION]["cab"]
URL = f"https://fwupd.org/downloads/{CAB_SHA256}-socfw.cab"
CAP_SHA256 = VERSIONS[VERSION]["capsule"]
UEFI_SHA256 = VERSIONS[VERSION]["uefi"]
DIRECTORY = ROOT / "firmware" / "prepared" / VERSION


def prepared_directory(version=DEFAULT_VERSION):
    if version not in VERSIONS:
        raise ValueError(f"unknown firmware version {version}; known: {sorted(VERSIONS)}")
    return ROOT / "firmware" / "prepared" / version
FMP_GUID = uuid.UUID("6dcbd5ed-e82d-4c44-bda1-7194199ad92a")
SOC_GUID = uuid.UUID("b488217b-3895-4fc0-b1bf-ab7005a2d45a")
# The 4.1.0 release ships one combined FMP image under a different type GUID; the
# payload behind it is the same MediaTek MSS1/SF_BOOT stream.
COMBINED_GUID = uuid.UUID("c8a8158c-76de-4129-a08d-d31bbea42c36")
DXE_GUID = uuid.UUID("d6a2cb7f-6a18-4e2f-b43b-9920a733700a")


def checked(data, offset, size):
    if offset < 0 or size < 0 or offset + size > len(data):
        raise ValueError("firmware container has an out-of-bounds field")
    return data[offset:offset + size]


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def capsule_payload(data):
    """Remove FMP/authentication/MSS1 wrappers, without claiming signature validation."""
    if checked(data, 0, 16) != FMP_GUID.bytes_le:
        raise ValueError("expected a UEFI FMP capsule")
    header_size, _, capsule_size = struct.unpack("<III", checked(data, 16, 12))
    if header_size < 28 or capsule_size != len(data):
        raise ValueError("invalid capsule size")
    version, drivers, images = struct.unpack("<IHH", checked(data, header_size, 8))
    if (version, drivers, images) != (1, 0, 1):
        raise ValueError("expected one FMP payload and no embedded drivers")
    item = struct.unpack("<Q", checked(data, header_size + 8, 8))[0]
    if item < 16:
        raise ValueError("FMP image overlaps its offset table")
    start = header_size + item
    image_header = checked(data, start, 48)
    if (struct.unpack_from("<I", image_header)[0] != 3 or
            image_header[4:20] not in (SOC_GUID.bytes_le, COMBINED_GUID.bytes_le)):
        raise ValueError("expected the Spark SoC FMP v3 image")
    image_size, vendor_size = struct.unpack_from("<II", image_header, 24)
    image = checked(data, start + 48, image_size)
    if vendor_size or start + 48 + image_size != len(data):
        raise ValueError("unexpected FMP trailing data")
    certificate_length = struct.unpack("<I", checked(image, 8, 4))[0]
    if certificate_length < 24:
        raise ValueError("invalid authentication certificate length")
    payload = checked(image, 8 + certificate_length, len(image) - 8 - certificate_length)
    if checked(payload, 0, 4) != b"MSS1":
        raise ValueError("expected an MSS1 payload header")
    length = struct.unpack("<I", checked(payload, 4, 4))[0]
    if length < 16:
        raise ValueError("invalid MSS1 header length")
    return checked(payload, length, len(payload) - length)


def unpack_uefi(payload, expected_hash=UEFI_SHA256):
    """expected_hash=None extracts and reports without a pinned comparison."""
    """Decode the MediaTek FILEINFO LZMA stream and verify the exact UEFI image."""
    if not payload.startswith(b"SF_BOOT"):
        raise ValueError("expected a MediaTek SF_BOOT image")
    signature = b"MMM\x00\x00\x02\x00\x00FILEINFO"
    offset = payload.find(signature)
    if offset < 0 or payload.find(signature, offset + 1) >= 0:
        raise ValueError("expected one MediaTek FILEINFO compression descriptor")
    decoder = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE, memlimit=128 * 1024 * 1024)
    result = decoder.decompress(payload[offset + 56:], max_length=32 * 1024 * 1024 + 1)
    if not decoder.eof or len(result) > 32 * 1024 * 1024:
        raise ValueError("truncated or oversized UEFI LZMA stream")
    if expected_hash is not None and sha256(result) != expected_hash:
        raise ValueError("extracted UEFI checksum mismatch")
    return result


def pe_info(image, offset):
    if checked(image, offset, 2) != b"MZ":
        raise ValueError("expected an MZ executable header")
    pe = offset + struct.unpack("<I", checked(image, offset + 60, 4))[0]
    if checked(image, pe, 6) != b"PE\x00\x00\x64\xaa":
        raise ValueError("expected an AArch64 PE executable")
    return {"entry_rva": struct.unpack("<I", checked(image, pe + 40, 4))[0],
            "image_base": struct.unpack("<Q", checked(image, pe + 48, 8))[0],
            "image_size": struct.unpack("<I", checked(image, pe + 80, 4))[0]}


def layout(image):
    if checked(image, 40, 4) != b"_FVH":
        raise ValueError("expected the outer UEFI firmware volume")
    sec = pe_info(image, 0x120)
    base = sec["image_base"] - 0x120
    instruction = struct.unpack("<I", checked(image, 0, 4))[0]
    immediate = instruction & 0x03ffffff
    if immediate & 0x02000000:
        immediate -= 0x04000000
    if instruction >> 26 != 5 or base + immediate * 4 != sec["image_base"] + sec["entry_rva"]:
        raise ValueError("reset branch and SEC entry do not agree")
    candidates = []
    offset = 0
    while (offset := image.find(DXE_GUID.bytes_le, offset)) >= 0:
        if checked(image, offset + 18, 1) == b"\x05" and checked(image, offset + 27, 1) == b"\x10":
            candidates.append(offset + 28)
        offset += 16
    if len(candidates) != 1:
        raise ValueError("expected exactly one uncompressed DXE core")
    dxe = pe_info(image, candidates[0])
    return {"firmware_base": base, "sec_entry": sec["image_base"] + sec["entry_rva"],
            "dxe_file_offset": candidates[0], "dxe_entry_rva": dxe["entry_rva"],
            "dxe_image_size": dxe["image_size"]}


def prepare(version=DEFAULT_VERSION):
    entry = VERSIONS[version]
    directory = prepared_directory(version)
    if directory.resolve() != directory:
        raise ValueError("prepared firmware directory must not traverse a symbolic link")
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("socfw.cap", "socfw.cap.partial", "uefi.fd", "layout.json"):
        path = directory / name
        if path.is_symlink():
            raise ValueError("prepared outputs must not be symbolic links")
        require_ignored_untracked(ROOT, path)
    url = f"https://fwupd.org/downloads/{entry['cab']}-socfw.cab"
    cab = ROOT / "firmware" / "downloads" / f"spark-soc-{version}.cab"
    if not cab.exists():
        cab = download(url, entry["cab"], cab.name)
    if cab.is_symlink() or sha256(cab.read_bytes()) != entry["cab"]:
        raise ValueError("local cabinet checksum mismatch")
    cap = directory / "socfw.cap"
    uefi = directory / "uefi.fd"
    if not cap.exists():
        seven_zip = shutil.which("7zz") or shutil.which("7z")
        if seven_zip is None:
            raise ValueError("install 7-Zip (7zz or 7z) to extract the local cabinet")
        temporary = directory / "socfw.cap.partial"
        try:
            with temporary.open("wb") as output:
                subprocess.run([seven_zip, "e", "-so", str(cab), "socfw.cap"],
                               stdout=output, stderr=subprocess.PIPE, check=True)
            if sha256(temporary.read_bytes()) != entry["capsule"]:
                raise ValueError("capsule checksum mismatch")
            temporary.replace(cap)
        finally:
            temporary.unlink(missing_ok=True)
    if cap.is_symlink() or sha256(cap.read_bytes()) != entry["capsule"]:
        raise ValueError("local capsule checksum mismatch")
    image = unpack_uefi(capsule_payload(cap.read_bytes()), entry["uefi"])
    if uefi.is_symlink():
        raise ValueError("firmware output must not be a symbolic link")
    uefi.write_bytes(image)
    record = {"version": version, "url": url, "cab_sha256": entry["cab"],
              "capsule_sha256": entry["capsule"], "uefi_sha256": sha256(image),
              "signature_verified": False, **layout(image)}
    (directory / "layout.json").write_text(json.dumps(record, indent=2) + "\n")
    return uefi, record


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="prepare a pinned Spark SoC release")
    parser.add_argument("--version", default=DEFAULT_VERSION, choices=sorted(VERSIONS))
    arguments = parser.parse_args()
    image, record = prepare(arguments.version)
    print(f"Prepared unchanged Spark SoC {arguments.version} UEFI: {image}")
    print(json.dumps(record, indent=2))
