"""Packet checks use only original synthetic packets, never captured firmware."""

import struct
import unittest

from tools.pxe import udp_packets, verify_capture

CLIENT, SERVER = bytes([10, 0, 2, 15]), bytes([10, 0, 2, 2])
MAC = bytes.fromhex("525400123456")


def udp(src, sport, dst, dport, data):
    ip = bytearray(20)
    ip[0], ip[9] = 0x45, 17
    struct.pack_into("!H", ip, 2, 28 + len(data))
    ip[12:16], ip[16:20] = src, dst
    return MAC + MAC + b"\x08\x00" + ip + struct.pack("!HHHH", sport, dport, len(data) + 8, 0) + data


def capture(frames):
    return struct.pack("<IHHIIII", 0xa1b2c3d4, 2, 4, 0, 0, 65536, 1) + b"".join(
        struct.pack("<IIII", 0, 0, len(frame), len(frame)) + frame for frame in frames)


def lease(kind, xid=b"ABCD"):
    data = bytearray(240)
    data[0:3] = bytes([1 if kind in (1, 3) else 2, 1, 6])
    data[4:8], data[28:34] = xid, MAC
    if kind in (2, 5):
        data[16:20] = CLIENT
    data[236:] = bytes.fromhex("63825363")
    data += bytes([53, 1, kind, 255])
    return udp(CLIENT, 68, SERVER, 67, data) if kind in (1, 3) else udp(SERVER, 67, CLIENT, 68, data)


def transfer(payload, *, ack=True):
    frames = [udp(CLIENT, 1301, SERVER, 69, b"\0\x01pxe-probe.efi\0octet\0blksize\x00512\0")]
    frames += [udp(SERVER, 69, CLIENT, 1301, b"\0\x06blksize\x00512\0")]
    for number, offset in enumerate(range(0, len(payload) + 1, 512), 1):
        frames.append(udp(SERVER, 69, CLIENT, 1301, struct.pack("!HH", 3, number) + payload[offset:offset + 512]))
        if ack:
            frames.append(udp(CLIENT, 1301, SERVER, 69, struct.pack("!HH", 4, number)))
    return frames


class PxeEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.payload = b"Original synthetic EFI transfer fixture. " * 29
        self.dhcp = [lease(kind) for kind in (1, 2, 3, 5)]

    def test_requires_dora_exact_bytes_and_final_ack(self):
        frames = self.dhcp + transfer(self.payload)
        result = verify_capture(capture(frames), self.payload)
        self.assertEqual(result["client_ip"], "10.0.2.15")
        self.assertEqual(result["tftp"]["bytes"], len(self.payload))
        self.assertEqual(result["tftp"]["blocks"], 3)
        for candidate in (frames[:-1], self.dhcp + transfer(self.payload[:-1]),
                          self.dhcp + transfer(self.payload, ack=False),
                          self.dhcp[:-1] + transfer(self.payload),
                          self.dhcp[:-1] + [lease(5, b"DIFF")] + transfer(self.payload)):
            with self.subTest(frames=len(candidate)), self.assertRaises(ValueError):
                verify_capture(capture(candidate), self.payload)

    def test_exact_multiple_needs_zero_length_final_block(self):
        payload = b"x" * 1024
        frames = self.dhcp + transfer(payload)
        self.assertEqual(verify_capture(capture(frames), payload)["tftp"]["blocks"], 3)
        with self.assertRaises(ValueError):
            verify_capture(capture(frames[:-2]), payload)

    def test_accepts_retransmission_but_rejects_changed_bytes(self):
        frames = self.dhcp + transfer(self.payload)
        frames.insert(-1, frames[-2])
        verify_capture(capture(frames), self.payload)
        frames[-2] = frames[-2][:-1] + bytes([frames[-2][-1] ^ 1])
        with self.assertRaisesRegex(ValueError, "inconsistent"):
            verify_capture(capture(frames), self.payload)

    def test_truncated_capture_and_wrong_link_are_rejected(self):
        valid = capture(self.dhcp)
        for invalid in (b"", valid[:23], valid[:-1], valid + b"x",
                        valid[:20] + struct.pack("<I", 101) + valid[24:]):
            with self.subTest(length=len(invalid)), self.assertRaises(ValueError):
                list(udp_packets(invalid))
