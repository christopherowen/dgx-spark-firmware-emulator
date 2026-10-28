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

VERSION = "2.158.15"
CAB_SHA256 = "f895952896ffed39fc167de0f86eab40609b5baf9debcf12caa720123a915416"
URL = f"https://fwupd.org/downloads/{CAB_SHA256}-socfw.cab"
CAP_SHA256 = "00393be197569851c9a72bd214cf22f45a04b7e1fc759ab4ccc68c98dd650d78"
UEFI_SHA256 = "568ff181d47fbfd31504a22d6e42975db77b99b02c07fe5c76823b484441e645"
DIRECTORY = ROOT / "firmware" / "prepared" / VERSION
FMP_GUID = uuid.UUID("6dcbd5ed-e82d-4c44-bda1-7194199ad92a")
SOC_GUID = uuid.UUID("b488217b-3895-4fc0-b1bf-ab7005a2d45a")
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
    if struct.unpack_from("<I", image_header)[0] != 3 or image_header[4:20] != SOC_GUID.bytes_le:
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
    if sha256(result) != expected_hash:
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


def prepare():
    directory = DIRECTORY
    if directory.resolve() != directory:
        raise ValueError("prepared firmware directory must not traverse a symbolic link")
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("socfw.cap", "socfw.cap.partial", "uefi.fd", "layout.json"):
        path = directory / name
        if path.is_symlink():
            raise ValueError("prepared outputs must not be symbolic links")
        require_ignored_untracked(ROOT, path)
    cab = ROOT / "firmware" / "downloads" / "spark-soc-2.158.15.cab"
    if not cab.exists():
        cab = download(URL, CAB_SHA256, cab.name)
    if cab.is_symlink() or sha256(cab.read_bytes()) != CAB_SHA256:
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
            if sha256(temporary.read_bytes()) != CAP_SHA256:
                raise ValueError("capsule checksum mismatch")
            temporary.replace(cap)
        finally:
            temporary.unlink(missing_ok=True)
    if cap.is_symlink() or sha256(cap.read_bytes()) != CAP_SHA256:
        raise ValueError("local capsule checksum mismatch")
    image = unpack_uefi(capsule_payload(cap.read_bytes()))
    if uefi.is_symlink():
        raise ValueError("firmware output must not be a symbolic link")
    uefi.write_bytes(image)
    record = {"version": VERSION, "url": URL, "cab_sha256": CAB_SHA256,
              "capsule_sha256": CAP_SHA256, "uefi_sha256": sha256(image),
              "signature_verified": False, **layout(image)}
    (directory / "layout.json").write_text(json.dumps(record, indent=2) + "\n")
    return uefi, record


if __name__ == "__main__":
    image, record = prepare()
    print(f"Prepared unchanged Spark SoC {VERSION} UEFI: {image}")
    print(json.dumps(record, indent=2))
