#!/usr/bin/env python3
"""Build this project's original lab monitor, without vendor firmware inputs."""

import argparse
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def find_tool(name):
    result = shutil.which(name)
    if result:
        return result
    for directory in (Path("/opt/homebrew/opt/llvm/bin"), Path("/usr/local/opt/llvm/bin")):
        if (directory / name).is_file():
            return str(directory / name)
    raise ValueError(f"install LLVM/Clang and LLD; required tool not found: {name}")


def build_monitor():
    output = ROOT / "build" / "monitor"
    if output.resolve() != output:
        raise ValueError("monitor build directory must not traverse symlinks")
    output.mkdir(parents=True, exist_ok=True)
    compiler, linker, objcopy = [find_tool(x) for x in ("clang", "ld.lld", "llvm-objcopy")]
    objects = []
    for source in ("start.S", "monitor.c", "nor.c"):
        obj = output / (source + ".o")
        subprocess.run([compiler, "--target=aarch64-none-elf", "-O2", "-ffreestanding",
                        "-fno-builtin", "-fno-stack-protector",
                        *([] if source.endswith(".S") else ["-mgeneral-regs-only"]),
                        "-mstrict-align", "-Wall", "-Wextra", "-Werror", "-c",
                        str(ROOT / "monitor" / source), "-o", str(obj)], check=True)
        objects.append(str(obj))
    executable = output / "monitor.elf"
    subprocess.run([linker, "-T", str(ROOT / "monitor" / "link.ld"), *objects,
                    "-o", str(executable)], check=True)
    binary = output / "monitor.bin"
    subprocess.run([objcopy, "-O", "binary", str(executable), str(binary)], check=True)
    if binary.stat().st_size >= 0xb0000:
        raise ValueError("monitor overlaps its reserved stacks")
    return binary


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(build_monitor())
