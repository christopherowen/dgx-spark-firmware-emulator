#!/usr/bin/env python3
"""Build the opt-in Spark firmware address profile; all build files remain ignored."""

import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import sys
from urllib.request import urlopen
import venv

ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = "https://github.com/qemu/qemu/archive/refs/tags/v11.1.2.tar.gz"
SOURCE_SHA256 = "6c7f81c186854db82542f0d56f63fc1ecf84e5416cf9ca2eb00b99673d75d36c"


def build(jobs):
    directory = ROOT / "build"
    if directory.resolve() != directory:
        raise ValueError("build directory must not traverse symbolic links")
    directory.mkdir(exist_ok=True)
    host = directory / "host-tools"
    if not (host / "bin" / "ninja").exists():
        venv.create(host, with_pip=True)
        subprocess.run([str(host / "bin" / "python"), "-m", "pip", "install", "ninja==1.13.2"], check=True)
    archive = directory / "qemu-v11.1.2.tar.gz"
    if not archive.exists():
        partial = archive.with_suffix(".partial")
        try:
            with urlopen(SOURCE_URL, timeout=30) as response, partial.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
            partial.replace(archive)
        finally:
            partial.unlink(missing_ok=True)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SOURCE_SHA256:
        raise ValueError("QEMU source archive checksum mismatch")
    source = directory / "qemu-src"
    patch = ROOT / "qemu" / "patches" / "0001-spark-fw-address-profile.patch"
    stamp = source / ".spark-patch-sha256"
    patch_hash = hashlib.sha256(patch.read_bytes()).hexdigest()
    if not source.exists():
        source.mkdir()
        subprocess.run(["tar", "-xzf", str(archive), "-C", str(source), "--strip-components=1"], check=True)
    if not stamp.exists():
        # Apply as a standalone source tree. Otherwise Git can discover the
        # parent lab repository and silently skip newly added files in build/.
        patch_environment = dict(os.environ, GIT_CEILING_DIRECTORIES=str(directory))
        subprocess.run(["git", "apply", "--check", str(patch)], cwd=source,
                       env=patch_environment, check=True)
        subprocess.run(["git", "apply", str(patch)], cwd=source,
                       env=patch_environment, check=True)
        stamp.write_text(patch_hash + "\n")
    elif stamp.read_text().strip() != patch_hash:
        raise ValueError("QEMU patch changed; remove build/qemu-src and rebuild")
    output = directory / "qemu-spark"
    output.mkdir(exist_ok=True)
    options = [str(source / "configure"), "--target-list=aarch64-softmmu",
               "--ninja=" + str(host / "bin" / "ninja"), "--disable-werror", "--disable-docs",
               "--disable-gtk", "--disable-sdl", "--disable-cocoa", "--disable-pvg",
               "--disable-opengl", "--disable-vnc", "--disable-tools", "--disable-capstone",
               "--enable-slirp", "--disable-gnutls", "--disable-libssh"]
    # Meson may regenerate build files during Ninja; it also needs Ninja on PATH.
    environment = dict(os.environ, PATH=str(host / "bin") + os.pathsep + os.environ.get("PATH", ""))
    subprocess.run(options, cwd=output, env=environment, check=True)
    subprocess.run([str(host / "bin" / "ninja"), "-C", str(output), "-j", str(jobs),
                    "qemu-system-aarch64"], env=environment, check=True)
    print(f"Built experimental QEMU: {output / 'qemu-system-aarch64'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, default=min(os.cpu_count() or 2, 8))
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("jobs must be positive")
    try:
        build(args.jobs)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
