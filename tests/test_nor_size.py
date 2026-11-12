import struct
import tempfile
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.handoff import boot_arguments
from tools.build_monitor import build_monitor

TOP = 0xc0000000


def regions(blob):
    count = struct.unpack_from("<I", blob, 0x78)[0]
    out = {}
    for index in range(count):
        offset = 0x80 + 56 * index
        name = blob[offset:offset + 56].split(b"\0", 1)[0].decode()
        base, size, flags = struct.unpack_from("<QQI", blob, offset + 32)
        out[name] = (base, size, flags)
    return out



class NorSizeTest(unittest.TestCase):
    def test_default_is_the_32_mib_model(self):
        self.assertEqual(boot_arguments(), boot_arguments(0x2000000))
        table = regions(boot_arguments())
        # Seed window, one state page, live window: the loader's copy is restored on every reset, so
        # the monitor serves flash from the second window and the tag has to keep both out of reach.
        self.assertEqual(table["MMAP_NOR_BACKING"], (0xa0000000, 0x4001000, 0))
        self.assertEqual(table["MMAP_FREE_BASE_3"], (0xa4001000, 0x1bfff000, 1))

    def test_64_mib_model_moves_the_free_region_and_keeps_the_top_of_ram(self):
        table = regions(boot_arguments(0x4000000))
        self.assertEqual(table["MMAP_NOR_BACKING"], (0xa0000000, 0x8001000, 0))
        free_base, free_size, flags = table["MMAP_FREE_BASE_3"]
        self.assertEqual((free_base, flags), (0xa8001000, 1))
        self.assertEqual(free_base + free_size, TOP)
        for name, (base, size, _flags) in table.items():
            if name != "MMAP_FREE_BASE_3":
                self.assertLessEqual(base + size, 0xa0000000 + 2 * 0x4000000 + 0x1000, name)

    def test_the_legacy_one_mib_model_is_still_supported(self):
        # The 1 MiB model predates the board's part; an existing fixture must
        # keep working, so its size is accepted everywhere a size is chosen.
        from tools.build_monitor import NOR_SIZES
        from tools.boot_spark import NOR_SIZES as RUNNER_SIZES
        self.assertIn(0x100000, NOR_SIZES)
        self.assertEqual(tuple(RUNNER_SIZES), tuple(NOR_SIZES))
        # the tag reserves both windows (seed and live) and the page that marks the live one seeded
        for size in (0x100000, 0x2000000, 0x4000000):
            self.assertEqual(regions(boot_arguments(size))["MMAP_NOR_BACKING"][1],
                             2 * size + 0x1000)

    def test_the_described_top_follows_the_allocation_past_four_gib(self):
        # The tag carries 64-bit bases and sizes, so the limit above 4 GiB is a policy, not a
        # format one. The description must still start at RAM_BASE, stay contiguous and end
        # exactly at the top it advertises.
        for megabytes, top in ((2048, 0xC0000000), (3072, 0x100000000), (8192, 0x240000000)):
            with self.subTest(megabytes=megabytes):
                table = regions(boot_arguments(0x4000000, ram_top=top))
                free_base, free_size, _flags = table["MMAP_FREE_BASE_3"]
                self.assertEqual(free_base + free_size, top)
                self.assertEqual(min(base for name, (base, _s, _f) in table.items()
                                     if name.startswith("MMAP_")), 0x80000000)
                ordered = sorted((base, base + size) for _n, (base, size, _f) in table.items()
                                 if _n.startswith("MMAP_"))
                for (start, end), (next_start, _next_end) in zip(ordered, ordered[1:]):
                    self.assertLessEqual(end, next_start, "regions must not overlap")

    def test_the_cli_rejects_an_unsupported_size(self):
        import subprocess
        for value in ("0x800000", "0x1000000"):
            result = subprocess.run([sys.executable, "tools/build_monitor.py", "--nor-size", value],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("nor_size must be one of", result.stderr + result.stdout)

    def test_resolver_takes_the_state_file_size_or_the_explicit_one(self):
        from tools.boot_spark import resolve_nor_size
        with tempfile.TemporaryDirectory() as directory:
            for size in (0x100000, 0x2000000, 0x4000000):
                state = Path(directory) / f"{size:#x}.bin"
                state.write_bytes(b"\xff" * size)
                self.assertEqual(resolve_nor_size(state), size)
                self.assertEqual(resolve_nor_size(state, 0x4000000), 0x4000000)
            self.assertEqual(resolve_nor_size(Path(directory) / "absent.bin"), 0x2000000)
            bad = Path(directory) / "bad.bin"
            bad.write_bytes(b"\xff" * 0x800000)
            with self.assertRaisesRegex(ValueError, "must be one of"):
                resolve_nor_size(bad)
            with self.assertRaisesRegex(ValueError, "must be one of"):
                resolve_nor_size(bad, 0x1000000)

    def test_resolver_follows_links_and_treats_a_dangling_one_as_absent(self):
        from tools.boot_spark import resolve_nor_size
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "real.bin"
            target.write_bytes(b"\xff" * 0x100000)
            link = Path(directory) / "link.bin"
            link.symlink_to(target)
            self.assertEqual(resolve_nor_size(link), 0x100000)
            dangling = Path(directory) / "dangling.bin"
            dangling.symlink_to(Path(directory) / "missing.bin")
            self.assertEqual(resolve_nor_size(dangling), 0x2000000)

    def test_cli_refusal_has_no_traceback(self):
        import subprocess
        result = subprocess.run([sys.executable, "tools/boot_spark.py", "--smoke-test",
                                 "--nor-size", "0x800000"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("must be one of", result.stderr)
        self.assertNotIn("Traceback", result.stderr + result.stdout)

    def test_board_and_nor_size_reject_mismatched_flash_image(self):
        import subprocess
        with tempfile.TemporaryDirectory() as directory:
            half = Path(directory) / "half.bin"
            half.write_bytes(b"\xff" * 0x2000000)
            for flags in (["--board", "--flash-image", str(half)],
                          ["--nor-size", "0x4000000", "--flash-image", str(half)],
                          ["--flash-image", str(Path(directory) / "missing.bin")]):
                result = subprocess.run([sys.executable, "tools/boot_spark.py", "--smoke-test", *flags],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 1)
                self.assertNotIn("Traceback", result.stderr + result.stdout)

    def test_an_explicit_size_is_checked_against_the_image_not_overwritten_by_it(self):
        """The image's length is the part; naming another size is refused, not silently applied.

        The layout has to be forced here. A 32 MiB file resolves to the store-only layout, which
        `--flash-image` refuses for its own reason, and that refusal would hide whether the explicit
        size was compared at all.
        """
        import subprocess
        with tempfile.TemporaryDirectory() as directory:
            half = Path(directory) / "half.bin"
            half.write_bytes(b"\xff" * 0x2000000)
            for size in ("0x100000", "0x4000000"):
                result = subprocess.run(
                    [sys.executable, "tools/boot_spark.py", "--smoke-test", "--flash-layout", "whole",
                     "--nor-size", size, "--flash-image", str(half)],
                    capture_output=True, text=True)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(f"not the modelled {int(size, 16):#x}", result.stderr)
                self.assertNotIn("Traceback", result.stderr + result.stdout)

    def test_a_marker_only_pxe_run_never_claims_a_transfer(self):
        from tools.boot_spark import RunMode, result_value
        self.assertEqual(result_value(RunMode.PXE_TEST, True),
                         "pxe-marker-only-exact-transfer-unverified")
        self.assertEqual(result_value(RunMode.PXE_TEST, False), RunMode.PXE_TEST.value)
        self.assertEqual(result_value(RunMode.SETUP_TEST, False), RunMode.SETUP_TEST.value)

    def test_monitor_rejects_an_unknown_flash_size(self):
        with self.assertRaises(ValueError):
            build_monitor("armed", nor_size=0x1000000)


if __name__ == "__main__":
    unittest.main()
