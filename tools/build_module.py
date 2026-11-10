#!/usr/bin/env python3
"""Build `kernel/spark_fan.c` against a real kernel header tree.

The module has to compile against the kernel that will load it, so this takes a header tree copied
from a unit and does the four things a module build needs that the headers package alone does not
provide on this host:

1. `scripts/basic/fixdep` ships as an aarch64 binary and cannot run here, so it is built for the host.
2. The unit's kernel was built with gcc, and its saved config passes gcc-only flags
   (`-fconserve-stack`, `-fsanitize=bounds-strict`) that clang rejects; a small shim strips the first
   and substitutes `-fsanitize=bounds` for the second, so sanitizer coverage differs from the unit's
   own build — this is a compile check, not a byte-identical reproduction.
3. `ARCH=arm64` is required: without it kbuild looks for x86 asm headers.
4. `scripts/mod/modpost` also ships as an aarch64 binary, so the module's modinfo is written by hand
   and the object is linked with `ld.lld -r`. The vermagic comes from `--vermagic`, which should be
   the unit's own (`modinfo -F vermagic` on one of its modules).

usage:
    build_module.py --headers ~/spark-headers/usr/src/linux-headers-6.17.0-1031-nvidia \\
        --vermagic "6.17.0-1031-nvidia SMP preempt mod_unload modversions aarch64"
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "kernel" / "spark_fan.c"
OUTPUT = ROOT / "kernel" / "spark_fan.ko"

SHIM = r'''#!/usr/bin/env python3
"""clang, minus the gcc-only flags this kernel's saved config passes."""
import os, sys
args = []
for argument in sys.argv[1:]:
    if argument == "-fconserve-stack":
        continue
    if argument == "-fsanitize=bounds-strict":
        args.append("-fsanitize=bounds")
        continue
    args.append(argument)
args.append("-Wno-unknown-warning-option")
os.execvp(os.environ.get("SPARK_CC", "clang"), [os.environ.get("SPARK_CC", "clang")] + args)
'''

MODINFO = '''/* The modinfo modpost would generate; see tools/build_module.py for why it is written here. */
#include <linux/module.h>
MODULE_INFO(vermagic, "%(vermagic)s");
MODULE_INFO(name, KBUILD_MODNAME);
MODULE_INFO(srcversion, "SPARKFANLOCALBUILD");
'''


def run(command, **kwargs):
    result = subprocess.run(command, capture_output=True, text=True, **kwargs)
    if result.returncode != 0:
        raise SystemExit(f"failed: {' '.join(str(part) for part in command)}\n"
                         f"{result.stdout[-2000:]}{result.stderr[-2000:]}")
    return result


# A kernel built with GCC passes flags clang rejects. Saying so beats "the object was not produced".
GCC_ONLY_FLAGS = ("unknown argument: '-fconserve-stack'", "unsupported argument 'bounds-strict'",
                  "unknown argument: '-mno-fp-ret-in-387'")


def diagnose(log: str) -> str:
    """Explain a failed kbuild in terms of what the target kernel was built with."""
    if any(marker in log for marker in GCC_ONLY_FLAGS):
        return ("this kernel was built with GCC, and its Makefile passes flags clang rejects. Build it where "
                "the kernel came from — `make -C /lib/modules/$(uname -r)/build M=<this>/kernel modules` — or "
                "against a full source tree with a GNU cross compiler. The header package alone cannot be "
                "cross-built: it ships host tools for the target architecture.")
    if "Exec format error" in log:
        return ("the header package's script binaries are built for the target architecture, so a kbuild pass "
                "cannot run here. Use a full kernel source tree, or build on the unit.")
    return "the module object was not produced; the make output is above — run make by hand to see why"


def build_fixdep(headers):
    fixdep = headers / "scripts" / "basic" / "fixdep"
    if fixdep.is_file() and "ELF 64-bit LSB pie" in subprocess.run(
            ["file", "-b", str(fixdep)], capture_output=True, text=True).stdout:
        return
    fixdep.unlink(missing_ok=True)
    run([os.environ.get("HOSTCC", "cc"), "-O2", "-I", str(headers / "scripts" / "include"),
         "-o", str(fixdep), str(headers / "scripts" / "basic" / "fixdep.c")])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--headers", type=Path, required=True,
                        help="a kernel header tree copied from the unit")
    parser.add_argument("--vermagic", required=True,
                        help="the unit's vermagic: modinfo -F vermagic on one of its modules")
    parser.add_argument("--out", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)

    headers = args.headers.resolve()
    if not (headers / "Makefile").is_file():
        raise SystemExit(f"{headers} does not look like a kernel header tree")
    clang = shutil.which("clang") or "clang"
    ld = shutil.which("ld.lld") or "ld.lld"

    with tempfile.TemporaryDirectory(prefix="spark-fan-build-") as directory:
        shim = Path(directory) / "ccshim"
        shim.write_text(SHIM)
        shim.chmod(0o755)
        build_fixdep(headers)
        # kbuild stops at modpost, which ships as an aarch64 binary; the object is what we want, so
        # a failure after `CC [M]` is expected and the object is checked for instead.
        result = subprocess.run(["make", "-C", str(headers), f"M={ROOT / 'kernel'}", "ARCH=arm64",
                                 "modules", f"CC={shim}", f"LD={ld}"], capture_output=True, text=True)
        module_object = ROOT / "kernel" / "spark_fan.o"
        if not module_object.is_file():
            raise SystemExit(diagnose(result.stdout + result.stderr))

        modinfo_source = Path(directory) / "spark_fan_mod.c"
        modinfo_source.write_text(MODINFO % {"vermagic": args.vermagic})
        modinfo_object = Path(directory) / "spark_fan_mod.o"
        common = [f"--target=aarch64-linux-gnu", "-D__KERNEL__", "-DMODULE",
                  '-DKBUILD_MODNAME="spark_fan"', "-Wno-unknown-warning-option"]
        for path in ("include", "arch/arm64/include", "include/generated",
                     "arch/arm64/include/generated"):
            common += ["-I", str(headers / path)]
        for header in ("linux/compiler-version.h", "linux/kconfig.h", "linux/compiler_types.h"):
            common += ["-include", str(headers / "include" / header)]
        run([str(shim), *common, "-c", str(modinfo_source), "-o", str(modinfo_object)])
        run([ld, "-r", "-o", str(args.out), str(module_object), str(modinfo_object)])

    print(f"{args.out}  {args.out.stat().st_size} bytes")
    details = subprocess.run(["modinfo", str(args.out)], capture_output=True, text=True,
                             check=False).stdout
    for line in details.splitlines():
        if line.startswith(("vermagic", "parm", "description")):
            print("  " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
