#!/usr/bin/env python3
"""Build the TCG plugin that logs one instruction's executions.

It compiles against the QEMU sources the profile was built from, so the plugin API matches the
binary that will load it. The result is cached by content hash: a plugin whose source changed
rebuilds, and a run can record exactly which plugin produced its trace.

usage: build_plugin.py [--source build/qemu-src] [--out build/spark-trace.so]
"""

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "tools" / "plugins" / "spark_trace.c"


def find_tool(name):
    result = shutil.which(name)
    if result:
        return result
    raise ValueError(f"install a C compiler and pkg-config; required tool not found: {name}")


def build(source=None, output=None):
    source = Path(source) if source else ROOT / "build" / "qemu-src"
    output = Path(output) if output else ROOT / "build" / "spark-trace.so"
    if not (source / "include" / "plugins" / "qemu-plugin.h").is_file():
        raise ValueError(f"{source} is not a QEMU source tree; build the profile first")
    if output.resolve() != output:
        raise ValueError("plugin output must not traverse symlinks")
    digest = hashlib.sha256(PLUGIN.read_bytes()).hexdigest()
    stamp = output.with_suffix(".sha256")
    if output.exists() and stamp.exists() and stamp.read_text().strip() == digest:
        return output
    flags = subprocess.run([find_tool("pkg-config"), "--cflags", "glib-2.0"],
                           capture_output=True, text=True, check=True).stdout.split()
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([find_tool("cc"), "-O2", "-fPIC", "-shared", "-Wall", "-Wextra", "-Werror",
                    "-I", str(source / "include"), "-I", str(source / "include" / "plugins"),
                    *flags, str(PLUGIN), "-o", str(output)], check=True)
    stamp.write_text(digest + "\n")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="QEMU source tree (default build/qemu-src)")
    parser.add_argument("--out", type=Path, help="plugin path (default build/spark-trace.so)")
    arguments = parser.parse_args()
    try:
        print(build(arguments.source, arguments.out))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
