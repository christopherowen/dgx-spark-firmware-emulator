"""Synthetic container, handoff, and debugger tests; no vendor firmware inputs."""

import hashlib
import lzma
import os
from pathlib import Path
import struct
import unittest

from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]

from tools.boot_spark import apply_board_preset, resolve_flash_layout, usable_megabytes
from tools.build_monitor import build_monitor, has_llvm_tools
from tools.handoff import diagnostic_boot_arguments
from tools.spark_image import FMP_GUID, SOC_GUID, capsule_payload, unpack_uefi


class BootInputTests(unittest.TestCase):
    def test_loader_tag_has_full_fixed_size_and_zero_terminator(self):
        data = diagnostic_boot_arguments()
        self.assertEqual(struct.unpack_from("<II", data), (0x1868, 0x88610005))
        self.assertEqual(len(data), 0x1868 + 8)
        self.assertEqual(data[-8:], bytes(8))
        count = struct.unpack_from("<I", data, 0x78)[0]
        entries = []
        for index in range(count):
            offset = 0x80 + 56 * index
            entries.append((data[offset:offset + 32].rstrip(b"\0"),
                            struct.unpack_from("<QQI", data, offset + 32)))
        self.assertIn((b"MMAP_TINY_UEFI", (0x80000000, 0x8000000, 1)), entries)
        self.assertIn((b"MMIO", (0, 0x40000000, 0)), entries)

    def test_capsule_unwrap_honors_offsets_and_rejects_truncation(self):
        payload = b"SF_BOOT" + bytes(80)
        mss1 = b"MSS1" + struct.pack("<III", 16, 1, 0) + payload
        auth = bytes(8) + struct.pack("<I", 24) + bytes(20)
        image = bytearray(48)
        struct.pack_into("<I", image, 0, 3)
        image[4:20] = SOC_GUID.bytes_le
        struct.pack_into("<II", image, 24, len(auth + mss1), 0)
        body = struct.pack("<IHHQ", 1, 0, 1, 16) + image + auth + mss1
        capsule = FMP_GUID.bytes_le + struct.pack("<III", 32, 0, 32 + len(body)) + bytes(4) + body
        self.assertEqual(capsule_payload(capsule), payload)
        for damaged in (capsule[:-1], capsule[:12], bytes(16) + capsule[16:]):
            with self.subTest(length=len(damaged)), self.assertRaises(ValueError):
                capsule_payload(damaged)

    def test_lzma_extraction_preserves_exact_image_bytes_and_checks_hash(self):
        image = b"Synthetic UEFI fixture" * 100
        descriptor = b"MMM\x00\x00\x02\x00\x00FILEINFO" + bytes(40)
        payload = b"SF_BOOT" + descriptor + lzma.compress(image, format=lzma.FORMAT_ALONE) + b"trailing"
        digest = hashlib.sha256(image).hexdigest()
        self.assertEqual(unpack_uefi(payload, digest), image)
        with self.assertRaisesRegex(ValueError, "checksum"):
            unpack_uefi(payload, "0" * 64)
        with self.assertRaises(ValueError):
            unpack_uefi(payload[:65], digest)


class FlashLayoutTests(unittest.TestCase):
    """The layout follows the input, so a file keeps meaning what its size says it is."""

    def test_a_32_mib_state_is_a_store(self):
        layout, size, offset, code = resolve_flash_layout(0x2000000)
        self.assertEqual((layout, offset, code), ("store", 0x3000000, None))

    def test_a_64_mib_state_is_the_whole_device(self):
        layout, size, offset, code = resolve_flash_layout(0x4000000)
        self.assertEqual((layout, size, offset, code), ("whole", 0x4000000, 0, 0x2000000))

    def test_an_absent_state_starts_as_the_board_device(self):
        # A new run should model the part rather than the old store window; an existing file keeps
        # the meaning its size gives it, so nothing already on disk is reinterpreted.
        layout, size, offset, code = resolve_flash_layout(None)
        self.assertEqual((layout, size, offset, code), ("whole", 0x4000000, 0, 0x2000000))
        self.assertEqual(resolve_flash_layout(None, "store")[0], "store")

    def test_an_explicit_zero_code_offset_survives(self):
        self.assertEqual(resolve_flash_layout(0x4000000, "whole", code_offset=0)[3], 0)

    def test_at_most_two_cpus_are_accepted(self):
        # The runner refuses a third CPU before it builds anything, so this needs no boot: the
        # message and the exit status are the whole contract.
        import subprocess, sys
        result = subprocess.run([sys.executable, "tools/boot_spark.py", "--smoke-test",
                                 "--smp", "3", "--timeout", "10"], cwd=ROOT,
                                capture_output=True, text=True,
                                env={**os.environ, "PATH": f"{ROOT / 'build' / 'host-tools' / 'bin'}:{os.environ['PATH']}"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("one or two CPUs", result.stderr + result.stdout)

    def test_the_legacy_layout_can_be_forced_on_a_whole_image(self):
        layout, _size, offset, code = resolve_flash_layout(0x4000000, "store")
        self.assertEqual((layout, offset, code), ("store", 0x3000000, None))

    def test_a_board_run_cannot_select_the_legacy_layout(self):
        with self.assertRaises(ValueError):
            resolve_flash_layout(0x4000000, "store", board=True)

    def test_an_unknown_layout_is_refused(self):
        with self.assertRaises(ValueError):
            resolve_flash_layout(0x4000000, "device")


class BoardPresetTests(unittest.TestCase):
    def preset(self, **fields):
        base = dict(flash_image=object(), code_offset=None, nor_size=None,
                    write_us=None, erase_us=None)
        base.update(fields)
        return SimpleNamespace(**base)

class BoardPresetTests(unittest.TestCase):
    def preset(self, **fields):
        base = dict(flash_image=object(), code_offset=None, nor_size=None,
                    write_us=None, erase_us=None)
        base.update(fields)
        return SimpleNamespace(**base)

    def test_preset_fills_the_whole_device_defaults(self):
        arguments = self.preset()
        apply_board_preset(arguments)
        self.assertEqual(arguments.code_offset, 0x2000000)
        self.assertEqual(arguments.nor_size, 0x4000000)
        self.assertEqual(arguments.write_us, 400)
        self.assertEqual(arguments.erase_us, 40000)

    def test_preset_leaves_an_explicit_zero_code_offset_alone(self):
        # Zero is a valid offset (the boot image at the start of the device), so the preset must
        # treat only "unset" as its own.
        arguments = self.preset(code_offset=0)
        apply_board_preset(arguments)
        self.assertEqual(arguments.code_offset, 0)

    def test_preset_keeps_what_the_caller_chose(self):
        arguments = self.preset(code_offset=0x1000, nor_size=0x2000000,
                                write_us=0, erase_us=7)
        apply_board_preset(arguments)
        self.assertEqual(arguments.code_offset, 0x1000)
        self.assertEqual(arguments.nor_size, 0x2000000)
        self.assertEqual(arguments.write_us, 0)
        self.assertEqual(arguments.erase_us, 7)

    def test_preset_needs_a_flash_image(self):
        with self.assertRaises(ValueError):
            apply_board_preset(self.preset(flash_image=None))


class UsableMemoryTests(unittest.TestCase):
    def test_usable_ram_follows_the_allocation_through_the_profile(self):
        # QEMU allocates from 0x40000000 and the profile aliases the low gigabyte to PCI BAR space,
        # so the guest's RAM starts at 0x80000000 and the handoff now describes it up to the end of
        # the allocation, capped at 4 GiB to stay clear of the machine's high MMIO windows.
        self.assertEqual(usable_megabytes(2048), 1024)   # 0x80000000..0xC0000000
        self.assertEqual(usable_megabytes(3072), 2048)   # ..0x100000000
        self.assertEqual(usable_megabytes(4096), 2048)   # capped at the 4 GiB mark
        self.assertEqual(usable_megabytes(131072), 2048) # the cap, not the board's 128 GB
        self.assertEqual(usable_megabytes(1536), 512)
        self.assertEqual(usable_megabytes(1024), 0)


@unittest.skipUnless(has_llvm_tools(), "a monitor build needs the LLVM host tools")
class MonitorTraceBuildTests(unittest.TestCase):
    def test_trace_and_ec_builds_are_different_binaries(self):
        # The diagnostic build must actually differ: the flag reaches the compiler, not just
        # the directory name.
        plain = build_monitor(nor_size=0x4000000, nor_offset=0, trace=False)
        traced = build_monitor(nor_size=0x4000000, nor_offset=0, trace=True)
        self.assertNotEqual(plain, traced)
        self.assertIn("flashlog", traced.name)
        self.assertNotEqual(plain.read_bytes(), traced.read_bytes())
        packet = build_monitor(nor_size=0x4000000, nor_offset=0, ffa_ec=True)
        self.assertIn("ffa-ec", packet.name)
        self.assertNotEqual(plain, packet)
        self.assertNotEqual(plain.read_bytes(), packet.read_bytes())

    def test_rebuilding_the_same_monitor_gives_the_same_bytes(self):
        # run.json records monitor_sha256 as evidence of which model produced a result; that only
        # means something if the same source rebuilds to the same bytes. The first build's bytes
        # are read before the second runs: both builds share one path, so reading afterwards would
        # compare the second build with itself and pass for any compiler.
        first = build_monitor(nor_size=0x4000000, nor_offset=0)
        first_bytes = first.read_bytes()
        self.assertEqual(build_monitor(nor_size=0x4000000, nor_offset=0), first)
        self.assertEqual(first_bytes, first.read_bytes())

    def test_mmap_notify_build_is_a_different_binary(self):
        # The opt-in service answer must reach the compiler too: it is the only thing that makes
        # the "measured inert" comparison a comparison.
        plain = build_monitor(nor_size=0x4000000, nor_offset=0)
        accepting = build_monitor(nor_size=0x4000000, nor_offset=0, accept_mmap_notify=True)
        self.assertIn("mmapok", accepting.name)
        self.assertNotEqual(plain.read_bytes(), accepting.read_bytes())
