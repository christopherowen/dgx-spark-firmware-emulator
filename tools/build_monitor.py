#!/usr/bin/env python3
"""Build this project's original lab monitor, without vendor firmware inputs."""

import argparse
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def find_tool(name):
    """Locate an LLVM tool, including distribution layouts that rename it.

    Debian and Ubuntu install `llvm-objcopy-18` and `/usr/lib/llvm-18/bin/llvm-objcopy`, never an
    unversioned `llvm-objcopy` on PATH. A caller that forgets to add those to PATH fails here with
    a message about installing LLVM, which is wrong: the tool is present under another name.
    """
    result = shutil.which(name)
    if result:
        return result
    for directory in (Path("/opt/homebrew/opt/llvm/bin"), Path("/usr/local/opt/llvm/bin"),
                      Path("/opt/homebrew/opt/lld/bin"), Path("/usr/local/opt/lld/bin")):
        if (directory / name).is_file():
            return str(directory / name)
    versioned = sorted(Path("/usr/lib").glob("llvm-*/bin/" + name), reverse=True)
    for candidate in versioned:
        if candidate.is_file():
            return str(candidate)
    prefix = name.split("-")[0]
    for suffix in sorted(Path("/usr/bin").glob(f"{prefix}-*-{name.split('-')[-1]}"), reverse=True):
        if suffix.is_file() and suffix.name.startswith(name):
            return str(suffix)
    raise ValueError(f"install LLVM/Clang and LLD; required tool not found: {name}")


def has_llvm_tools(*names):
    try:
        for name in (names or ("clang", "ld.lld", "llvm-objcopy")):
            find_tool(name)
        return True
    except ValueError:
        return False


POLICIES = {"armed": [], "open": ["-DSPARK_NOR_POLICY_OPEN"]}
# A lab build may run the readback client at the point a DXE driver would, i.e. before the trusted
# endpoint arms the lockout at ReadyToBoot. It is a model of that stage, not a second entry point.

# 1 MiB is the size the model had before the board's part was modelled: it stays
# supported so an existing fixture boots unchanged. 32 MiB is the default,
# 64 MiB is the board's own part.
NOR_SIZES = (0x100000, 0x2000000, 0x4000000)


def latency_flags(write_us, erase_us=None):
    for name, value in (("write", write_us), ("erase", erase_us)):
        if value is not None and (value < 0 or value > 200000):
            raise ValueError(f"{name} latency must be between 0 and 200000 microseconds")
    flags = [] if write_us == 0 else [f"-DNOR_WRITE_US={write_us}"]
    if erase_us is not None:
        flags.append(f"-DNOR_ERASE_US={erase_us}")
    return flags


def build_monitor(policy="armed", write_us=0, erase_us=None, nor_size=0x2000000,
                  nor_offset=0x3000000, trace=False, accept_mmap_notify=False,
                  gwdt_timeout_ms=None, ram_top=None, rtc_base=None, code_offset=None,
                  code_size=None, program_model="page", cpus=1, ffa_ec=False):
    if policy not in POLICIES:
        raise ValueError(f"unknown flash policy: {policy}")
    if nor_size not in NOR_SIZES:
        raise ValueError(
            "nor_size must be one of "
            + ", ".join(f"{value:#x}" for value in NOR_SIZES))
    flags = POLICIES[policy] + latency_flags(write_us, erase_us)
    if cpus not in (1, 2):
        raise ValueError("the spark-fw-lab machine models one or two CPUs")
    flags = flags + [f"-DSPARK_CPU_COUNT={cpus}"]
    suffix = policy
    if cpus != 1:
        # The cache key has to carry it: a one-CPU and a two-CPU monitor differ only in this flag, so
        # without it the second build silently reuses the first one's binary.
        suffix = suffix + f"-cpu{cpus}"
    if write_us or erase_us:
        suffix = "{0}-{1}us-{2}erus".format(policy, write_us, erase_us)
    if nor_size != 0x2000000:
        flags = flags + [f"-DNOR_SIZE={nor_size:#x}UL"]
        suffix = suffix + f"-nor{nor_size >> 20}m"
    if nor_offset != 0x3000000:
        flags = flags + [f"-DNOR_OFFSET={nor_offset:#x}UL"]
        suffix = suffix + f"-off{nor_offset:#x}"
    if trace:
        # Diagnostic builds: the flash service prints each answered request, and the exception
        # handler prints each SMC that reaches it.
        flags = flags + ["-DNOR_TRACE=1", "-DMONITOR_TRACE=1"]
        suffix = suffix + "-flashlog"
    if gwdt_timeout_ms:
        # Pre-arm the GWDT the way a BL31 would: a timeout in WOR, WCS still clear.
        flags = flags + [f"-DSPARK_GWDT_TIMEOUT_MS={int(gwdt_timeout_ms)}"]
        suffix = suffix + f"-gwdt{int(gwdt_timeout_ms)}ms"
    if code_offset is not None and code_size is not None:
        # The monitor copies the boot image out of the live flash window on every start, so a code
        # byte the firmware programmed is what the next boot executes.
        flags = flags + [f"-DSPARK_CODE_OFFSET={int(code_offset):#x}UL",
                         f"-DSPARK_CODE_SIZE={int(code_size):#x}UL"]
        suffix = suffix + f"-code{int(code_offset):#x}-size{int(code_size):#x}"
    if program_model not in ("page", "byte"):
        raise ValueError(f"unknown program model: {program_model}")
    if program_model == "byte":
        # Every byte of a pulse is committed when its share of the page budget is spent, so a
        # reset part-way through leaves a prefix written.
        flags = flags + ["-DNOR_PROGRAM_MODEL=1"]
        suffix = suffix + "-byteprog"
    if ram_top is not None and ram_top != 0xc0000000:
        # The monitor must accept buffers anywhere the loader tag says RAM is, or a larger
        # allocation advertises memory the FF-A path refuses to map.
        flags = flags + [f"-DRAM_TOP={int(ram_top):#x}UL"]
        suffix = suffix + f"-top{int(ram_top):#x}"
    if rtc_base is not None:
        # Both clocks in a run must be the pinned one: QEMU's -rtc and the monitor's own service.
        flags = flags + [f"-DRTC_BASE={int(rtc_base)}UL"]
        suffix = suffix + f"-rtc{int(rtc_base)}"
    if accept_mmap_notify:
        # Experimental: answer the firmware's DRAM notification with success instead of
        # NOT_SUPPORTED, so the two answers can be compared boot for boot.
        flags = flags + ["-DSPARK_ACCEPT_MMAP_NOTIFY=1"]
        suffix = suffix + "-mmapok"
    if ffa_ec:
        flags = flags + ["-DSPARK_FFA_EC=1"]
        suffix = suffix + "-ffa-ec"
    output = ROOT / "build" / "monitor-{0}".format(suffix)
    if output.resolve() != output:
        raise ValueError("monitor build directory must not traverse symlinks")
    output.mkdir(parents=True, exist_ok=True)
    compiler, linker, objcopy = [find_tool(x) for x in ("clang", "ld.lld", "llvm-objcopy")]
    objects = []
    for source in ("start.S", "monitor.c", "nor.c", "fan.c", "spbm.c", "ffa_memory.c"):
        obj = output / (source + ".o")
        subprocess.run([compiler, "--target=aarch64-none-elf", "-O2", "-ffreestanding",
                        "-fno-builtin", "-fno-stack-protector",
                        *([] if source.endswith(".S") else ["-mgeneral-regs-only"]),
                        "-mstrict-align", "-Wall", "-Wextra", "-Werror", *flags, "-c",
                        str(ROOT / "monitor" / source), "-o", str(obj)], check=True)
        objects.append(str(obj))
    executable = output / "monitor.elf"
    subprocess.run([linker, "-T", str(ROOT / "monitor" / "link.ld"), *objects,
                    "-o", str(executable)], check=True)
    binary = output / ("monitor-{0}.bin".format(suffix))
    subprocess.run([objcopy, "-O", "binary", str(executable), str(binary)], check=True)
    if binary.stat().st_size >= 0xb0000:
        raise ValueError("monitor overlaps its reserved stacks")
    return binary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gwdt-timeout-ms", type=int, default=None,
                        help="pre-arm the GWDT with this timeout, as a BL31 would")
    parser.add_argument("--accept-mmap-notify", action="store_true",
                        help="answer 0xc4000122 with success instead of NOT_SUPPORTED")
    parser.add_argument("--flash-trace", action="store_true",
                        help="build a monitor that logs each answered flash request")
    parser.add_argument("--ffa-ec", action="store_true",
                        help="publish the experimental Linux FF-A EC packet service")
    parser.add_argument("--flash-policy", choices=sorted(POLICIES), default="armed",
                        help="armed matches the board; open models the pre-arming window")
    parser.add_argument("--nor-size", type=lambda v: int(v, 0), default=0x2000000,
                        help="0x100000 (legacy model), 0x2000000 (default) or 0x4000000 (board)")
    parser.add_argument("--nor-offset", type=lambda v: int(v, 0), default=0x3000000,
                        help="offset of the modelled flash region inside the device; 0 models the "
                             "whole device, as the board-faithful mode needs")
    parser.add_argument("--write-us", type=int, default=0,
                        help="modeled program latency in microseconds (default 0)")
    parser.add_argument("--erase-us", type=int, default=None,
                        help="modeled erase latency in microseconds (default: same as --write-us)")
    args = parser.parse_args()
    print(build_monitor(args.flash_policy, args.write_us, args.erase_us,
                        args.nor_size, args.nor_offset, args.flash_trace,
                        args.accept_mmap_notify, args.gwdt_timeout_ms, ffa_ec=args.ffa_ec))
