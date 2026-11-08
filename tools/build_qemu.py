#!/usr/bin/env python3
"""Build the opt-in Spark firmware address profile; all build files remain ignored."""

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.request import urlopen
import venv

ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = "https://github.com/qemu/qemu/archive/refs/tags/v11.1.2.tar.gz"
SOURCE_SHA256 = "6c7f81c186854db82542f0d56f63fc1ecf84e5416cf9ca2eb00b99673d75d36c"
SOURCE_GIT_URL = "https://github.com/qemu/qemu.git"
SOURCE_TAG = "v11.1.2"
SOURCE_COMMIT = "4fc49f46dc95d4a27de2509e7fceb2931e91faeb"
SOURCE_TREE = "0e6dfe70f3ae7b3ffc985c183e9df1b7ae3df4de"


def unpack_source(directory, archive, source):
    if not archive.exists():
        partial = archive.with_suffix(".partial")
        try:
            with urlopen(SOURCE_URL, timeout=60) as response, partial.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
            partial.replace(archive)
        except OSError:
            partial.unlink(missing_ok=True)
    if archive.exists():
        if hashlib.sha256(archive.read_bytes()).hexdigest() != SOURCE_SHA256:
            raise ValueError("QEMU source archive checksum mismatch")
        source.mkdir()
        subprocess.run(["tar", "-xzf", str(archive), "-C", str(source), "--strip-components=1"], check=True)
        return
    repo = directory / "qemu-git"
    shutil.rmtree(repo, ignore_errors=True)
    try:
        subprocess.run(["git", "clone", "--depth", "1", "--branch", SOURCE_TAG, "--no-checkout",
                        SOURCE_GIT_URL, str(repo)], check=True)
        commit = subprocess.check_output(["git", "-C", str(repo), "rev-parse", f"{SOURCE_TAG}^{{commit}}"],
                                         text=True).strip()
        tree = subprocess.check_output(["git", "-C", str(repo), "rev-parse", f"{SOURCE_TAG}^{{tree}}"],
                                       text=True).strip()
        if (commit, tree) != (SOURCE_COMMIT, SOURCE_TREE):
            raise ValueError(f"QEMU git tag verification mismatch: got {commit} / {tree}")
        source.mkdir()
        archive_proc = subprocess.Popen(["git", "-C", str(repo), "archive", "--format=tar", SOURCE_COMMIT],
                                        stdout=subprocess.PIPE)
        subprocess.run(["tar", "-xf", "-", "-C", str(source)], stdin=archive_proc.stdout, check=True)
        archive_proc.stdout.close()
        if archive_proc.wait() != 0:
            raise subprocess.CalledProcessError(archive_proc.returncode, "git archive")
    finally:
        shutil.rmtree(repo, ignore_errors=True)


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
    source = directory / "qemu-src"
    patch = ROOT / "qemu" / "patches" / "0001-spark-fw-address-profile.patch"
    stamp = source / ".spark-patch-sha256"
    patch_hash = hashlib.sha256(patch.read_bytes()).hexdigest()
    if not source.exists():
        unpack_source(directory, archive, source)
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
