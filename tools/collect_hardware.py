#!/usr/bin/env python3
"""Collect read-only Spark inventory over SSH into ignored local firmware/hardware/."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.download_firmware import require_ignored_untracked


REMOTE = r'''
import io,json,subprocess,sys,tarfile
from pathlib import Path

records={}
errors={}
def read(name,path,limit=2*1024*1024):
    try:
        with Path(path).open('rb') as stream:
            data=stream.read(limit+1)
        if len(data)>limit:
            raise ValueError('capture size limit exceeded')
        records[name]=data
    except (OSError,ValueError) as error:
        errors[name]=str(error)

for name in ('sys_vendor','product_name','product_version','bios_version'):
    read('board/'+name, '/sys/class/dmi/id/'+name)
for name in ('iomem','interrupts'):
    read(name+'.txt','/proc/'+name)
base=Path('/sys/firmware/acpi/tables')
for path in sorted(base.rglob('*')):
    if path.is_file() and not path.is_symlink():
        read('acpi/'+str(path.relative_to(base)),path)
for device in sorted(Path('/sys/bus/acpi/devices').glob('*')):
    for name in ('path','hid','status'):
        path=device/name
        if path.is_file():
            read('acpi-devices/'+device.name+'/'+name,path,4096)
commands={
    'uname.txt':['uname','-a'],
    'cpu.json':['lscpu','-J'],
    'pci.txt':['lspci','-nn'],
    'firmware-devices.json':['fwupdmgr','get-devices','--json'],
    'kernel-log.txt':['dmesg','--color=never'],
}
for name,command in commands.items():
    try:
        result=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=20)
        if len(result.stdout)>8*1024*1024:
            raise ValueError('command capture size limit exceeded')
        records[name]=result.stdout
        if result.returncode:
            errors[name]={'exit_code':result.returncode,'stderr':result.stderr.decode(errors='replace')}
    except (OSError,ValueError,subprocess.TimeoutExpired) as error:
        errors[name]=str(error)
records['capture-errors.json']=(json.dumps(errors,indent=2)+'\n').encode()
with tarfile.open(fileobj=sys.stdout.buffer,mode='w|gz') as archive:
    for name,data in sorted(records.items()):
        info=tarfile.TarInfo(name)
        info.size=len(data)
        info.mode=0o600
        archive.addfile(info,io.BytesIO(data))
'''


def collect(host):
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@:-]*", host):
        raise ValueError("provide an SSH host alias or user@host")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    output = ROOT / "firmware" / "hardware" / stamp
    if output.resolve() != output:
        raise ValueError("hardware output must not traverse symbolic links")
    output.mkdir(parents=True, mode=0o700)
    archive_path = output / "capture.tar.gz"
    require_ignored_untracked(ROOT, archive_path)
    with archive_path.open("wb") as stream:
        os.chmod(archive_path, 0o600)
        subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                        "--", host, "sudo -n python3 -"], input=REMOTE.encode(),
                       stdout=stream, stderr=subprocess.PIPE, timeout=120, check=True)
    count = 0
    with tarfile.open(archive_path) as archive:
        for member in archive:
            target = output / member.name
            if not member.isfile() or member.size > 8 * 1024 * 1024:
                raise ValueError("unexpected capture archive member")
            if not target.resolve().is_relative_to(output.resolve()):
                raise ValueError("capture archive path escapes its destination")
            require_ignored_untracked(ROOT, target)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with target.open("wb") as stream:
                os.chmod(target, 0o600)
                stream.write(archive.extractfile(member).read())
            count += 1
    metadata = output / "source.json"
    require_ignored_untracked(ROOT, metadata)
    metadata.write_text(json.dumps({"ssh_host": host, "captured_at_utc": stamp,
                                    "read_only": True, "files": count}, indent=2) + "\n")
    os.chmod(metadata, 0o600)
    print(f"Captured {count} local inventory files: {output}")
    print("Raw captures stay Git-ignored. No direct device-register access or firmware updates were performed.")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", help="SSH host; read access uses sudo -n on the remote host")
    args = parser.parse_args()
    try:
        collect(args.host)
    except (OSError, ValueError, subprocess.SubprocessError, tarfile.TarError) as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
