#!/usr/bin/env python3
"""Download a checksum-pinned upstream artifact into ignored firmware/downloads/."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


REPO_ROOT = Path(__file__).resolve().parents[1]


def require_https(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("use a direct HTTPS upstream download URL without embedded credentials")


class HTTPSRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        require_https(new_url)
        return super().redirect_request(request, response, code, message, headers, new_url)


def open_https(url):
    require_https(url)
    return build_opener(HTTPSRedirects()).open(
        Request(url, headers={"User-Agent": "dgx-spark-firmware-emulator-downloader/1"}), timeout=30
    )


def require_ignored_untracked(root, path):
    relative = str(path.relative_to(root))
    tracked = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--error-unmatch", "--", relative],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False
    )
    if tracked.returncode != 1:
        raise ValueError(f"download destination is tracked or Git could not verify it: {relative}")
    ignored = subprocess.run(
        ["git", "-C", str(root), "check-ignore", "--quiet", "--no-index", "--", relative],
        check=False
    )
    if ignored.returncode != 0:
        raise ValueError(f"download destination must be Git-ignored: {relative}")


def download(url, sha256, filename, root=REPO_ROOT):
    require_https(url)
    if not re.fullmatch(r"[0-9a-fA-F]{64}", sha256):
        raise ValueError("SHA-256 must be exactly 64 hexadecimal characters from upstream metadata")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}", filename):
        raise ValueError("filename must be a plain filename with no directory components")
    root = Path(root).resolve()
    directory = root / "firmware" / "downloads"
    if directory.resolve() != directory:
        raise ValueError("firmware/downloads must not traverse a symbolic link")
    target = directory / filename
    metadata = directory / (filename + ".source.json")
    for path in (target, metadata):
        if path.is_symlink():
            raise ValueError("download files must not be symbolic links")
        require_ignored_untracked(root, path)
    if target.exists() or metadata.exists():
        raise ValueError("destination already exists; choose a new filename or remove the local copy")
    directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    temporary = []
    try:
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".download-", delete=False) as output:
            temporary.append(Path(output.name))
            with open_https(url) as response:
                final_url = response.geturl()
                require_https(final_url)
                while chunk := response.read(1024 * 1024):
                    digest.update(chunk)
                    output.write(chunk)
                    size += len(chunk)
        if digest.hexdigest() != sha256.lower():
            raise ValueError("SHA-256 mismatch; downloaded bytes were discarded")
        record = {"url": url, "resolved_url": final_url, "sha256": digest.hexdigest(),
                  "size_bytes": size, "filename": filename}
        with tempfile.NamedTemporaryFile(mode="w", dir=directory, prefix=".source-",
                                         encoding="utf-8", delete=False) as output:
            temporary.append(Path(output.name))
            json.dump(record, output, indent=2)
            output.write("\n")
        os.replace(temporary[0], target)
        os.replace(temporary[1], metadata)
        return target
    finally:
        for path in temporary:
            path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="direct vendor/LVFS HTTPS artifact URL")
    parser.add_argument("--sha256", required=True, help="expected checksum from upstream metadata")
    parser.add_argument("--filename", required=True, help="local filename, for example spark-soc.cab")
    args = parser.parse_args()
    try:
        target = download(args.url, args.sha256, args.filename)
        print(f"Saved verified firmware locally: {target}")
        print("Firmware and source metadata are Git-ignored; do not redistribute them.")
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
