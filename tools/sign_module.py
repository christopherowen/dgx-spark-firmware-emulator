#!/usr/bin/env python3
"""Sign the fan module with a key the firmware will accept.

The module is a build product and is never committed; a *signed* module is the same build product plus
a trailer, and the private key stays outside the repository. So the signed copy is produced here, on
the host that holds the key, and the operator then enrols the matching certificate as a MOK.

Lockdown at `integrity` is why this exists: an unsigned module is refused with *Key was rejected by
service*, and `CONFIG_MODULE_SIG_FORCE` being unset does not change that.

    python3 tools/sign_module.py --key ~/.keys/spark-lab/spark_mok.key \
                                 --cert ~/.keys/spark-lab/spark_mok.crt

The signer is the kernel's own `scripts/sign-file`, which is not installed with this host's kernel; it
is compiled from the header package the unit's kernel ships, because a signature made by a different
implementation is a signature nobody has verified.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "kernel" / "spark_fan.ko"
SIGNED = ROOT / "kernel" / "spark_fan.signed.ko"
SIGN_FILE_SOURCE = ("firmware/downloads/unit-headers/usr/src/linux-headers-6.17.0-1031-nvidia/"
                    "scripts/sign-file.c")
LOCAL_SIGN_FILE = ROOT / "work" / "sign" / "sign-file"


def sign_file() -> Path:
    """Return a runnable sign-file, building it from the unit's kernel source when needed."""
    if LOCAL_SIGN_FILE.exists():
        return LOCAL_SIGN_FILE
    source = ROOT / SIGN_FILE_SOURCE
    if not source.exists():
        raise SystemExit(f"cannot find the kernel's sign-file source at {SIGN_FILE_SOURCE}; "
                         "copy a unit's kernel header package into firmware/downloads/unit-headers/")
    if not shutil.which("cc"):
        raise SystemExit("a C compiler is required to build sign-file")
    LOCAL_SIGN_FILE.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["cc", "-O2", "-o", str(LOCAL_SIGN_FILE), str(source), "-lcrypto"], check=True)
    return LOCAL_SIGN_FILE


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--key", type=Path, required=True, help="private key (never commit it)")
    parser.add_argument("--cert", type=Path, required=True, help="certificate matching the key")
    parser.add_argument("--module", type=Path, default=MODULE)
    parser.add_argument("--out", type=Path, default=SIGNED)
    parser.add_argument("--hash", default="sha512")
    arguments = parser.parse_args()

    for path, what in ((arguments.key, "private key"), (arguments.cert, "certificate"),
                       (arguments.module, "module")):
        if not path.exists():
            raise SystemExit(f"{what} not found: {path}")
    if arguments.out == arguments.module:
        raise SystemExit("refusing to sign in place: the unsigned build must stay on disk")

    arguments.out.write_bytes(arguments.module.read_bytes())
    subprocess.run([str(sign_file()), arguments.hash, str(arguments.key), str(arguments.cert),
                    str(arguments.out)], check=True)

    signer = subprocess.run(["modinfo", "-F", "signer", str(arguments.out)],
                            capture_output=True, text=True).stdout.strip()
    if not signer:
        raise SystemExit("the module came out unsigned")
    print(f"{arguments.out.name}: signed by {signer} "
          f"({arguments.out.stat().st_size} bytes, was {arguments.module.stat().st_size})")
    print(f"enrol the certificate first: mokutil --import <the certificate as DER>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
