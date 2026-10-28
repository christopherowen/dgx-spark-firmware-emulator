"""Original EFI probe builder and packet-level PXE evidence checks."""

import hashlib
from pathlib import Path
import re
import struct
import subprocess

from tools.build_monitor import ROOT, find_tool


def build_probe(directory, token):
    if not re.fullmatch(r"[0-9a-f]{32}", token):
        raise ValueError("probe token must be 32 lowercase hexadecimal digits")
    directory = Path(directory)
    obj, image = directory / "pxe-probe.obj", directory / "pxe-probe.efi"
    subprocess.run([find_tool("clang"), "--target=aarch64-pc-windows-msvc", "-O2",
                    "-ffreestanding", "-fno-builtin", "-fno-stack-protector",
                    "-Wall", "-Wextra", "-Werror", f'-DPROBE_TOKEN=L"{token}"',
                    "-c", str(ROOT / "uefi/pxe_probe.c"), "-o", str(obj)], check=True)
    subprocess.run([find_tool("lld-link"), "/subsystem:efi_application", "/entry:EfiMain",
                    "/nodefaultlib", "/machine:arm64", "/timestamp:0", f"/out:{image}",
                    str(obj)], check=True)
    return image


def udp_packets(capture):
    """Read bounded Ethernet/IPv4/UDP packets from QEMU's classic PCAP output."""
    if len(capture) < 24 or capture[:4] not in (b"\xd4\xc3\xb2\xa1", b"\xa1\xb2\xc3\xd4"):
        raise ValueError("expected a classic PCAP capture")
    endian = "<" if capture[:4] == b"\xd4\xc3\xb2\xa1" else ">"
    major, minor, _, _, snaplen, link = struct.unpack_from(endian + "HHIIII", capture, 4)
    if (major, minor, link) != (2, 4, 1) or not 14 <= snaplen <= 16 * 1024 * 1024:
        raise ValueError("expected PCAP 2.4 with Ethernet frames")
    offset = 24
    while offset < len(capture):
        if offset + 16 > len(capture):
            raise ValueError("truncated PCAP record")
        _, _, length, original = struct.unpack_from(endian + "IIII", capture, offset)
        offset += 16
        if length > snaplen or length > original or offset + length > len(capture):
            raise ValueError("invalid or truncated PCAP frame")
        frame = capture[offset:offset + length]
        offset += length
        if len(frame) < 34 or frame[12:14] != b"\x08\x00":
            continue
        ip = frame[14:]
        header = (ip[0] & 15) * 4
        total = int.from_bytes(ip[2:4], "big")
        if ip[0] >> 4 != 4 or header < 20 or total < header + 8 or total > len(ip):
            raise ValueError("invalid IPv4 packet")
        if ip[9] != 17 or int.from_bytes(ip[6:8], "big") & 0x3fff:
            continue
        source, destination, length, _ = struct.unpack_from("!HHHH", ip, header)
        if length < 8 or header + length > total:
            raise ValueError("invalid UDP datagram")
        yield (ip[12:16], source, ip[16:20], destination, ip[header + 8:header + length])


def dhcp_type(data, mac):
    if len(data) < 240 or data[1:3] != b"\x01\x06" or data[28:34] != mac:
        return None
    if data[236:240] != b"\x63\x82\x53\x63":
        return None
    offset = 240
    while offset < len(data):
        option = data[offset]
        offset += 1
        if option == 255:
            break
        if option == 0:
            continue
        if offset >= len(data) or offset + 1 + data[offset] > len(data):
            raise ValueError("truncated DHCP option")
        length = data[offset]
        offset += 1
        value = data[offset:offset + length]
        offset += length
        if option == 53 and length == 1:
            return value[0]
    return None


def verify_capture(capture, payload, filename="pxe-probe.efi", mac=bytes.fromhex("525400123456")):
    """Require DHCP DORA and a fully acknowledged, exact TFTP payload download."""
    packets = list(udp_packets(capture))
    transactions = {}
    for src, sport, dst, dport, data in packets:
        if (sport, dport) not in ((68, 67), (67, 68)):
            continue
        kind = dhcp_type(data, mac)
        if kind not in (1, 2, 3, 5) or data[0] != (1 if sport == 68 else 2):
            continue
        transactions.setdefault(data[4:8], {})[kind] = data
    leases = [entry for entry in transactions.values() if set(entry) == {1, 2, 3, 5} and
              entry[2][16:20] == entry[5][16:20] != b"\0" * 4]
    if not leases:
        raise ValueError("capture has no complete matching DHCP discover/offer/request/ACK")
    lease = leases[-1][5][16:20]
    sessions = {}
    completed = None
    for src, sport, dst, dport, data in packets:
        if len(data) < 2:
            continue
        opcode = int.from_bytes(data[:2], "big")
        if dport == 69 and opcode == 1 and src == lease:
            fields = data[2:].split(b"\0")
            if len(fields) >= 3 and fields[:2] == [filename.encode("ascii"), b"octet"]:
                sessions[(src, sport, dst)] = {"blocks": {}, "size": 512, "server_port": None}
            continue
        key = (dst, dport, src)
        session = sessions.get(key)
        if session and opcode in (3, 6):
            if session["server_port"] not in (None, sport):
                continue
            session["server_port"] = sport
            if opcode == 6:
                fields = data[2:].split(b"\0")
                if fields[-1] != b"" or len(fields) % 2 != 1:
                    raise ValueError("malformed TFTP option acknowledgement")
                options = dict(zip(fields[:-1:2], fields[1::2]))
                session["size"] = int(options.get(b"blksize", b"512"))
                if not 8 <= session["size"] <= 65464:
                    raise ValueError("invalid TFTP block size")
            elif len(data) >= 4:
                number = int.from_bytes(data[2:4], "big")
                block = data[4:]
                if not number or len(block) > session["size"] or number > len(payload) // 8 + 1:
                    raise ValueError("invalid TFTP data block")
                if number in session["blocks"] and session["blocks"][number] != block:
                    raise ValueError("inconsistent TFTP retransmission")
                session["blocks"][number] = block
            continue
        session = sessions.get((src, sport, dst))
        if not session or opcode != 4 or len(data) != 4 or dport != session["server_port"]:
            continue
        last = int.from_bytes(data[2:4], "big")
        blocks = session["blocks"]
        if (last and set(blocks) == set(range(1, last + 1)) and
                len(blocks[last]) < session["size"] and
                all(len(blocks[i]) == session["size"] for i in range(1, last))):
            downloaded = b"".join(blocks[i] for i in range(1, last + 1))
            if downloaded == payload:
                completed = {"blocks": last, "block_size": session["size"],
                             "bytes": len(downloaded), "sha256": hashlib.sha256(downloaded).hexdigest()}
    if completed is None:
        raise ValueError("capture has no complete, acknowledged TFTP download matching the EFI payload")
    return {"dhcp_dora_verified": True, "client_ip": ".".join(map(str, lease)),
            "tftp": completed, "udp_packets": len(packets)}
