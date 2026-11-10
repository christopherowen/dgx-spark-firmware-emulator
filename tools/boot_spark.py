#!/usr/bin/env python3
"""Boot unchanged Spark UEFI/MM to AMI setup or verify native IPv4 PXE."""

import argparse
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from enum import Enum
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import select
import selectors
import shutil
import subprocess
import sys
import tempfile
import termios
import time
import tty

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.build_monitor import NOR_SIZES, build_monitor
from tools.download_firmware import require_ignored_untracked
from tools.dxe_protocols import inspect_protocols
from tools.handoff import boot_arguments
from tools.prepare_mm import MM_SHA256, prepare_mm
from tools.pxe import build_probe, verify_capture
from tools.qemu_control import Qmp, connect_unix

# The default model is the 32 MiB flash every stored run was taken on. The board's
# own part is 64 MiB (MX77U51255FZ4I42); pass --nor-size 0x4000000 for that arm.
NOR_STATE_SIZE = 0x2000000
from tools.spark_image import DEFAULT_VERSION, VERSIONS, prepared_directory


class RunMode(Enum):
    INTERACTIVE = "interactive-session-ended"
    SETUP_TEST = "setup-and-keyboard-verified"
    PXE_TEST = "pxe-download-and-execution-verified"


def local_path(path):
    path = Path(os.path.abspath(path))
    if not path.is_relative_to(ROOT / "firmware") or path.resolve() != path:
        raise ValueError("local firmware/state paths must be inside ignored firmware/, without symlinks")
    require_ignored_untracked(ROOT, path)
    return path


@contextmanager
def terminal_raw(enabled):
    saved = termios.tcgetattr(sys.stdin) if enabled else None
    try:
        if enabled:
            tty.setraw(sys.stdin.fileno())
        yield
    finally:
        if saved:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, saved)


def result_value(mode, marker_only: bool) -> str:
    """The run record's verdict: a marker-only PXE run never claims a transfer."""
    if mode is RunMode.PXE_TEST and marker_only:
        return "pxe-marker-only-exact-transfer-unverified"
    return mode.value


def resolve_nor_size(state: Path, explicit=None) -> int:
    """Pick the modelled flash size: explicit wins, else the state file's own size.

    Nothing here may silently fall back to a size the monitor cannot build, so an
    unsupported value, and a state file of unsupported size, are errors that name
    the accepted sizes.
    """
    if explicit is not None:
        if explicit not in NOR_SIZES:
            raise ValueError(
                "flash size must be one of "
                + ", ".join(f"{value:#x}" for value in NOR_SIZES)
                + f", not {explicit:#x}")
        return explicit
    if state.is_file():
        size = state.stat().st_size
        if size not in NOR_SIZES:
            raise ValueError(
                f"{state} is {size} bytes; a state file must be one of "
                + ", ".join(f"{value:#x}" for value in NOR_SIZES))
        return size
    return NOR_STATE_SIZE


def usable_megabytes(allocated_mb: int) -> int:
    """How much RAM the guest is told it has, from what QEMU allocated.

    QEMU puts RAM at 0x40000000; the profile aliases PCI BAR space over the low gigabyte, so the
    guest's RAM starts at 0x80000000. The loader handoff stops describing RAM at
    `handoff.RAM_TOP`, so an allocation above that adds memory the firmware never learns about.
    """
    from tools.handoff import RAM_BASE
    low = 0x40000000
    top = min(low + allocated_mb * 1024 * 1024, 0x100000000)
    return max(0, (top - RAM_BASE) // (1024 * 1024))


WHOLE_DEVICE_SIZE = 0x4000000


def resolve_flash_layout(state_size, layout="auto", board=False, code_offset=None,
                         nor_size=None):
    """Decide which flash layout a run uses, from the input alone.

    `auto` reads the state file's own size: a whole device is 64 MiB and carries both
    volumes, so a 32 MiB file can only be a store. Nothing is rebound by a default —
    a file keeps meaning what its size says it is — while a whole-device image gets
    the board's offsets without being asked.

    Returns `(layout, nor_size, nor_offset, code_offset)`.
    """
    if layout not in ("auto", "whole", "store"):
        raise ValueError(f"unknown flash layout: {layout}")
    # auto: an existing file keeps the meaning its size gives it, and an absent state starts as the
    # board's device — a new run should model the part, not the old store window.
    whole = (layout == "whole"
             or (layout == "auto" and (state_size == WHOLE_DEVICE_SIZE or state_size is None)))
    if board and layout == "store":
        raise ValueError("--board needs the whole-device layout; drop --flash-layout store")
    if whole:
        return ("whole", nor_size or WHOLE_DEVICE_SIZE, 0,
                0x2000000 if code_offset is None else code_offset)
    return ("store", nor_size, 0x3000000, None)


def apply_board_preset(args):
    """Fill in `--board`'s defaults, keeping anything the caller set explicitly.

    Separate from `main` so the defaults can be tested without starting a machine.
    """
    if args.flash_image is None:
        raise ValueError("--board needs --flash-image: the whole-device image to boot from")
    # None, not zero: an explicit --code-offset 0 selects the start of the device and must survive
    # the preset, so the parser keeps "unset" distinct from a value the caller chose.
    if args.code_offset is None:
        args.code_offset = 0x2000000
    if args.nor_size is None:
        args.nor_size = 0x4000000
    # Datasheet-typical times for the board's part: a 256-byte page programs in
    # about 0.4 ms, a 4 KiB sector erases in about 40 ms.
    if args.write_us is None:
        args.write_us = 400
    if args.erase_us is None:
        args.erase_us = 40000


def nvme_arguments(images, path_for=local_path):
    """The QEMU arguments for the NVMe namespaces, and one receipt entry per namespace.

    Each image is its own PCI function on bus 1 (slot 2, then 3, …) and is snapshotted, so a run can
    never write the caller's file. Two namespaces are what installing the recovery system needs: the
    media that carries the kernel, and the disk the installer partitions.
    """
    arguments = []
    entries = []
    for index, image in enumerate(Path(image) for image in (images or [])):
        if index >= 4:
            raise ValueError("at most four NVMe namespaces are modelled (one PCI function each)")
        if not image.is_file():
            raise ValueError(f"NVMe image not found: {image}")
        arguments += ["-drive",
                      f"if=none,id=nvme{index},file={path_for(image)},format=raw,snapshot=on",
                      "-device", f"nvme,bus=pcie.0,addr={2 + index},drive=nvme{index},"
                                 f"serial=SPARKLAB{index + 1:02d}"]
        # Streamed, not read whole: a target disk is gigabytes and reading it into memory is how a
        # boot becomes an OOM kill instead of a run.
        digest = hashlib.sha256()
        with image.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        entries.append({"path": str(image),
                        "sha256": digest.hexdigest(),
                        "size": image.stat().st_size,
                        "pci": f"synthetic: segment 0 bus 1 slot {2 + index}"})
    return arguments, entries


def run(qemu, mode, timeout, state_path, policy="armed", write_us=0, erase_us=None, pxe_image=None,
        pxe_marker=None, rtc_base=None, seed=None, firmware_version=DEFAULT_VERSION,
        memory=None, smp=None, nor_size=NOR_STATE_SIZE, gdb_port=None,
        allow_marker_only=False, nor_offset=0x3000000, code_offset=None, trace=False,
        accept_mmap_notify=False, gwdt_timeout_ms=None, stop_after_boots=None,
        program_model=None, code_offset_source="not-sliced", trace_pc=None, trace_limit=0, trace_sample=0,
        extended_ram=False, nvme_images=None):
    qemu = shutil.which(qemu)
    if qemu is None:
        raise ValueError("build the Spark QEMU profile first: python3 tools/build_qemu.py")
    # Prepare the release before anything sizes it: the monitor's code copy needs the boot image's
    # length, and a first use of a version would otherwise stat a file that does not exist yet.
    package = prepare_mm(firmware_version)
    # Both knobs are decided before the monitor is built because they are compile-time: the RAM top
    # the tag will advertise, and the clock the firmware's own RTC service reports.
    # The tag carries 64-bit region bases and sizes, so describing RAM above 4 GiB is a decision,
    # not a format limit. It stays opt-in: the machine's high MMIO windows are not modelled here, so
    # a description that reaches past 4 GiB is only honest when the caller says so.
    described = 0x40000000 + parse_megabytes(memory or "2G") * 1024 * 1024
    ram_top = described if extended_ram else min(described, 0x100000000)
    # A whole-device run models the part's pulse by default, because that is the layout whose
    # results can talk about power loss; the store-only layout keeps the older page-at-once model.
    if program_model is None:
        program_model = "byte" if nor_offset == 0 else "page"
    monitor = build_monitor(policy, write_us, erase_us, nor_size, nor_offset, trace,
                            accept_mmap_notify, gwdt_timeout_ms, ram_top=ram_top,
                            rtc_base=epoch_seconds(rtc_base), code_offset=code_offset,
                            program_model=program_model,
                            code_size=(prepared_directory(firmware_version) / "uefi.fd").stat().st_size
                            if code_offset is not None else None)
    prepared_firmware = prepared_directory(firmware_version) / "uefi.fd"
    firmware_path = prepared_firmware
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    directory = local_path(ROOT / "firmware" / "runs" / stamp)
    directory.mkdir(parents=True)
    handoff = local_path(directory / "bootargs.bin")
    # QEMU maps RAM from 0x40000000 and the profile aliases the low gigabyte to PCI BAR space, so the
    # guest's RAM runs 0x80000000..(0x40000000 + allocation). Tell the firmware that, so a larger
    # --memory is described instead of being hidden behind the original 0xc0000000 ceiling. The cap
    # at 4 GiB keeps the tag clear of the machine's high MMIO windows, whose bases this harness does
    # not model.
    handoff.write_bytes(boot_arguments(nor_size, ram_top=ram_top))
    state = local_path(state_path)
    state.parent.mkdir(parents=True, exist_ok=True)
    lock_path = local_path(state.with_suffix(state.suffix + ".lock"))
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not state.exists():
            # A fresh state is created at the path the caller asked for, under the lock, and after
            # the release is prepared (run() calls prepare_mm() before this). A whole-device state
            # gets the erased store *and* the release's code volume; a store gets erased bytes.
            with state.open("xb") as output:
                if nor_offset == 0:
                    from tools.board_image import CODE_SIZE, fresh_image
                    code = (prepared_directory(firmware_version) / "uefi.fd").read_bytes()
                    if len(code) > CODE_SIZE:
                        raise ValueError(f"the prepared release is {len(code):#x} bytes; the code "
                                         f"volume is {CODE_SIZE:#x}")
                    output.write(fresh_image(code))
                else:
                    output.write(b"\xff" * nor_size)
            state.chmod(0o600)
        if state.stat().st_size != nor_size:
            raise ValueError("NOR state must be exactly the modelled flash size; preserve it and select another state file")
        before = hashlib.sha256(state.read_bytes()).hexdigest()
        if code_offset is not None:
            # The whole device is modelled, so the boot image must come out of the same bytes a guest
            # write would land in; otherwise a write to the code region could never change what boots.
            # Read it here, under the lock, so the code half and the flash half are one generation.
            image = state.read_bytes()
            length = prepared_firmware.stat().st_size
            if code_offset < 0 or code_offset + length > len(image):
                raise ValueError("--code-offset does not fit the flash image")
            firmware_path = local_path(directory / "uefi-from-flash.fd")
            firmware_path.write_bytes(image[code_offset:code_offset + length])
        return run_locked(qemu=qemu, monitor=monitor, package=package, directory=directory,
                          handoff=handoff, state=state, before=before, mode=mode, timeout=timeout,
                          pxe_image=pxe_image, pxe_marker=pxe_marker, rtc_base=rtc_base,
                          seed=seed, firmware_version=firmware_version, memory=memory, smp=smp,
                          erase_us=erase_us, write_us=write_us, policy=policy,
                          nor_size=nor_size, gdb_port=gdb_port, trace=trace,
                          accept_mmap_notify=accept_mmap_notify, gwdt_timeout_ms=gwdt_timeout_ms,
                          nor_offset=nor_offset, code_offset=code_offset,
                          stop_after_boots=stop_after_boots, program_model=program_model,
                          code_offset_source=code_offset_source,
                          trace_pc=trace_pc, trace_limit=trace_limit, trace_sample=trace_sample, extended_ram=extended_ram,
                          nvme_images=nvme_images,
                          allow_marker_only=allow_marker_only, firmware_path=firmware_path)


def run_locked(qemu, monitor, package, directory, handoff, state, before, mode, timeout,
               pxe_image=None, pxe_marker=None, rtc_base=None, seed=None,
               firmware_version=DEFAULT_VERSION, memory=None, smp=None, erase_us=None,
               write_us=None, policy="armed", nor_size=NOR_STATE_SIZE, gdb_port=None,
               trace=False, accept_mmap_notify=False, gwdt_timeout_ms=None,
               nor_offset=0x3000000, code_offset=None, stop_after_boots=None,
               allow_marker_only=False, firmware_path=None, program_model=None,
               code_offset_source="not-sliced", trace_pc=None, trace_limit=0, trace_sample=0,
               extended_ram=False, nvme_images=None):
    firmware_directory = prepared_directory(firmware_version)
    if firmware_path is None:
        firmware_path = firmware_directory / "uefi.fd"
    # The boot image is either the prepared release or bytes sliced out of the flash image, so the
    # record hashes the file that was loaded rather than the version it was asked for: a code-volume
    # edit must show up here or the record names firmware that did not run.
    loaded_sha256 = hashlib.sha256(firmware_path.read_bytes()).hexdigest()
    pinned_sha256 = VERSIONS[firmware_version]["uefi"]
    record = {"mode": mode.name.lower(), "timeout": timeout, "firmware_version": firmware_version,
              "nor_size": nor_size,
              "uefi_sha256": loaded_sha256,
              "uefi_pinned_sha256": pinned_sha256,
              "mm_sha256": MM_SHA256[firmware_version],
              "firmware_modified": loaded_sha256 != pinned_sha256, "setup_visible": False,
              "keyboard_navigation_verified": False, "state_sha256_before": before,
              "state_path": str(state), "monitor_sha256": hashlib.sha256(monitor.read_bytes()).hexdigest(),
              # The machine is part of the result too: record which QEMU build ran it.
              "qemu_sha256": hashlib.sha256(Path(qemu).read_bytes()).hexdigest(),
              "flash_policy": policy}
    interactive = mode is RunMode.INTERACTIVE and sys.stdin.isatty()
    marker = None
    marker_only = False
    if mode is RunMode.PXE_TEST:
        tftp = local_path(directory / "tftp")
        tftp.mkdir()
        token = secrets.token_hex(16)
        # Only this one application is exposed through TFTP. Logs, state, vendor
        # images and compiler outputs stay outside the served directory.
        if pxe_image is not None:
            source = Path(pxe_image)
            image_bytes = source.read_bytes()
            if image_bytes[:2] != b"MZ":
                raise ValueError(f"{source} is not a PE image")
            payload = local_path(tftp / source.name)
            payload.write_bytes(image_bytes)
            record["pxe_image"] = str(source)
            record["pxe_network_supplied"] = True
            if pxe_marker:
                marker = pxe_marker.encode()
                record["pxe_marker"] = pxe_marker
        else:
            payload = build_probe(directory, token)
            shutil.copyfile(payload, local_path(tftp / payload.name))
            marker = ("SPARK_PXE_EXECUTED:" + token).encode("ascii")
        record["pxe_execution_verified"] = False
        record["pxe_probe_sha256"] = hashlib.sha256(payload.read_bytes()).hexdigest()
    capture = bytearray()
    process = None
    partial = local_path(state.with_suffix(state.suffix + ".partial"))
    qmp = None
    with (
        tempfile.TemporaryDirectory(prefix="spark-") as temporary,
        ExitStack() as resources,
        local_path(directory / "serial.log").open("wb") as log,
    ):
        command = [qemu, "-M", "virt,spark-fw-lab=on,spark-rot-error-replies=on,"
                   "gic-version=3,virtualization=on,secure=on", "-accel", "tcg",
                   "-cpu", "max,sve=off,sme=off,pauth=off",
                   "-m", memory or "2G", "-smp", str(smp or 1),
                   "-nodefaults", "-display", "none", "-monitor", "none", "-serial", "stdio",
                   "-qmp", f"unix:{temporary}/qmp,server=on,wait=off",
                   # cpu_reset is what makes a reboot explainable: the watchdog, which is
                   # what resets the board-faithful image, logs its expiry there.
                   "-d", "guest_errors,unimp,cpu_reset", "-D",
                   str(local_path(directory / "qemu.log"))]
        # Diagnostic only: hold the CPU at reset and expose a GDB stub so a driver can
        # break on a firmware address while the guest runs. Never used for receipts.
        if gdb_port:
            command += ["-S", "-gdb", f"tcp:127.0.0.1:{gdb_port}"]
        # Two runs of one store differ only in values the guest draws from the
        # clock or from the RNG, so both inputs are pinnable for comparison.
        if rtc_base is not None:
            command += ["-rtc", f"base={rtc_base}"]
        if seed is not None:
            command += ["-seed", str(seed)]
        record["rtc_base"] = rtc_base
        record["seed"] = seed
        record["memory"] = memory or "2G"
        # What the guest can actually use: the profile aliases PCI BAR space over the lowest
        # gigabyte, so RAM starts at 0x80000000 and stops one gigabyte short of the allocation.
        allocated = parse_megabytes(memory or "2G")
        # MiB, not a rounded gigabyte: 2100 MiB of allocation is 1076 MiB usable, and "1G" would
        # understate what the guest was told.
        record["memory_usable"] = f"{usable_megabytes(allocated)}M"
        record["ram_description"] = "extended" if extended_ram else "capped-at-4g"
        record["erase_latency_us"] = erase_us
        record["write_latency_us"] = write_us
        record["smp"] = smp or 1
        record["secondary_cpus"] = ("parked until started; no PSCI implementation in the monitor"
                                    if (smp or 1) > 1 else "none")
        # Every knob that changes what the guest sees or what the model does, recorded so a result
        # is reproducible from its own record: the flash offset decides whole-device vs partition-only,
        # the code offset decides which bytes the boot image was sliced from, and the diagnostic
        # switches change what the monitor prints or answers.
        record["nor_offset"] = nor_offset
        # Which layout this run used, and which receipt schema it is written in: a record from
        # before these fields existed was taken on the store-only layout.
        record["receipt_schema"] = 2
        # Which policy resolved the layout: 1 read the file's size only, 2 also starts an absent
        # state as the board's device. A receipt from schema 1 says the older thing.
        record["layout_policy"] = 2
        record["flash_layout"] = "whole" if nor_offset == 0 else "store"
        record["code_offset_source"] = code_offset_source
        # A version, not a boolean: a result about a partly written page must say how the
        # partiality was modelled.
        record["program_model"] = ("linear-request-byte-v1" if program_model == "byte"
                                   else "page-before-delay-v1")
        if stop_after_boots is not None:
            record["stop_after_boots"] = stop_after_boots
        record["code_offset"] = code_offset
        record["flash_trace"] = bool(trace)
        record["accept_mmap_notify"] = bool(accept_mmap_notify)
        if gwdt_timeout_ms:
            record["gwdt_timeout_ms"] = gwdt_timeout_ms
        if mode is RunMode.PXE_TEST:
            command += ["-netdev", f"user,id=pxe,restrict=on,tftp={tftp},bootfile={payload.name}",
                        "-device", "spark-rtl8168,netdev=pxe,addr=1,mac=52:54:00:12:34:56",
                        "-object", "filter-dump,id=pxe-capture,netdev=pxe,file=" +
                        str(local_path(directory / "packets.pcap"))]
        else:
            command += ["-nic", "none"]
        arguments, entries = nvme_arguments(nvme_images)
        command += arguments
        if entries:
            record.setdefault("nvme_images", []).extend(entries)
            record["nvme_write_policy"] = "snapshot-per-run; no source file is written"
            record["nvme_image"] = entries[0]["path"]   # kept: one image was the only shape before
        if trace_pc or trace_sample:
            from tools.build_plugin import build as build_plugin
            plugin = build_plugin()
            options = f"pc={trace_pc:#x}" if trace_pc is not None else ""
            if trace_sample:
                options += ("," if options else "") + f"sample={int(trace_sample)}"
            command += ["-plugin", f"{plugin},{options},limit={int(trace_limit)}"]
            record["trace_pc"] = trace_pc
            if trace_sample:
                record["trace_sample"] = trace_sample
            record["trace_limit"] = int(trace_limit)
            record["plugin_sha256"] = hashlib.sha256(plugin.read_bytes()).hexdigest()
        for image, address, cpu in ((monitor, 0x80020000, True), (package, 0x93a40000, False),
                                    (firmware_path, 0x80800000, False),
                                    (handoff, 0x80010000, False), (state, 0xa0000000, False)):
            command += ["-device", f"loader,file={image},addr={address:#x},force-raw=on" +
                        (",cpu-num=0" if cpu else "")]
        record["command"] = command
        try:
            process = resources.enter_context(subprocess.Popen(
                command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, bufsize=0))
            qmp = resources.enter_context(Qmp(connect_unix(Path(temporary) / "qmp", process)))
            selector = resources.enter_context(selectors.DefaultSelector())
            selector.register(process.stdout, selectors.EVENT_READ, "uart")
            if interactive:
                selector.register(sys.stdin, selectors.EVENT_READ, "keyboard")
            deadline = time.monotonic() + timeout
            escape_sent = False
            navigate_at = stop_at = None
            navigation_offset = None
            with terminal_raw(interactive):
                while process.poll() is None:
                    now = time.monotonic()
                    if now >= deadline and stop_at is None and not (interactive and record["setup_visible"]):
                        # Drain first: a marker can be sitting in the pipe when the clock runs out, and
                        # a run whose evidence already arrived has not timed out.
                        drained = []
                        while True:
                            ready, _, _ = select.select([process.stdout], [], [], 0)
                            if not ready:
                                break
                            pending = os.read(process.stdout.fileno(), 65536)
                            if not pending:
                                break
                            drained.append(pending)
                        for pending in drained:
                            log.write(pending)
                            log.flush()
                            capture += pending
                        target = "PXE download/execution" if mode is RunMode.PXE_TEST else "setup/navigation"
                        verified, wanted, detail = final_cycle_verdict(mode, capture, navigation_offset, marker)
                        record["deadline_verdict"] = {"verified": verified, **detail}
                        apply_verdict(record, mode, detail, verified)
                        if not verified:
                            raise TimeoutError(f"{target} not reached before the boot timeout")
                        # The evidence was already in the pipe when the clock ran out: finish through
                        # the normal stop path, where the guard and the record are written.
                        stop_at = 0
                        continue
                    if stop_at is not None and now >= stop_at:
                        break
                    if navigate_at is not None and now >= navigate_at:
                        process.stdin.write(b"\x1b[C")
                        navigation_offset = len(capture)
                        navigate_at = None
                    for key, _ in selector.select(.05):
                        if key.data == "keyboard":
                            data = os.read(sys.stdin.fileno(), 4096)
                            if b"\x03" in data:
                                stop_at = 0
                            else:
                                process.stdin.write(data)
                            continue
                        data = os.read(process.stdout.fileno(), 65536)
                        if not data:
                            continue
                        log.write(data)
                        log.flush()
                        capture += data
                        if mode is RunMode.INTERACTIVE:
                            sys.stdout.write(data.decode("cp437"))
                            sys.stdout.flush()
                        boots_now = capture.count(b"MON: boot STMM")
                        if stop_after_boots is not None:
                            # Count monitor hand-overs rather than wall-clock cycles: on a loaded host
                            # a fixed deadline cuts an arbitrary number of reboot cycles, so a class
                            # count taken that way varies with load. This stops at a boot count.
                            if boots_now > stop_after_boots:
                                record["boots_observed"] = boots_now
                                stop_at = 0
                        chunk_start = len(capture) - len(data)
                        # A marker can straddle two reads, so search with an overlap rather than
                        # from the start of this chunk: half a screen title is not a screen.
                        def marker_at(needle, start=None):
                            """Absolute index of `needle`, or -1.

                            The search starts 64 bytes before this chunk: a marker can be split
                            across two reads, and half a screen title is not a screen. With an
                            explicit `start` (the navigation offset) the search is from there.
                            """
                            overlap = max(64, len(needle) - 1)
                            return capture.find(needle, max(0, chunk_start - overlap) if start is None
                                                else start)

                        def cycle_of(position):
                            """Which boot cycle an event at this capture position belongs to."""
                            return capture.count(b"MON: boot STMM", 0, position)

                        def verified_in(boots):
                            note_verified(record, boots)

                        if b"Synchronous Exception" in capture or b"MON: abort" in capture:
                            raise RuntimeError("firmware exception; see this run's local serial.log")
                        if mode is not RunMode.PXE_TEST and not escape_sent and b"Press <DEL>" in capture:
                            process.stdin.write(b"\x1b")
                            escape_sent = True
                        setup_at = marker_at(b"Aptio Setup - AMI")
                        if not record["setup_visible"] and setup_at >= 0:
                            record["setup_visible"] = True
                            # Which boot cycle saw it: a reset after this point means the setup
                            # screen belonged to an earlier cycle, not to the state the snapshot is
                            # taken from, and a stop must not claim it as this boot's.
                            record["setup_boot"] = cycle_of(setup_at)
                            if mode is RunMode.SETUP_TEST:
                                navigate_at = now + 1
                        navigation_at = (marker_at(b"Power On Behavior", navigation_offset)
                                         if navigation_offset is not None else -1)
                        if (mode is RunMode.SETUP_TEST and navigation_at >= 0 and
                                not record["keyboard_navigation_verified"]):
                            record["keyboard_navigation_verified"] = True
                            record["keyboard_boot"] = cycle_of(navigation_at)
                            verified_in(record["keyboard_boot"])
                            if stop_at is None:
                                stop_at = now + 2
                        marker_at_pxe = marker_at(marker) if (mode is RunMode.PXE_TEST and marker) else -1
                        if marker_at_pxe >= 0 and not record["pxe_execution_verified"]:
                            record["pxe_execution_verified"] = True
                            record["pxe_boot"] = cycle_of(marker_at_pxe)
                            verified_in(record["pxe_boot"])
                            if stop_at is None:
                                stop_at = now + .5
            if process.poll() is not None:
                raise RuntimeError("QEMU exited unexpectedly; see serial.log")
            qmp.request("stop")
            # Drain what the guest wrote before it was stopped: a reset can land between the last
            # read and the stop, and a final cycle whose banner is still in the pipe would otherwise
            # inherit the previous cycle's verification.
            while True:
                ready, _, _ = select.select([process.stdout], [], [], 0)
                if not ready:
                    break
                pending = os.read(process.stdout.fileno(), 65536)
                if not pending:
                    break
                log.write(pending)
                log.flush()
                capture += pending
                if mode is RunMode.INTERACTIVE:
                    sys.stdout.write(pending.decode("cp437"))
                    sys.stdout.flush()
            boots_now = capture.count(b"MON: boot STMM")
            if b"Synchronous Exception" in capture or b"MON: abort" in capture:
                raise RuntimeError("firmware exception after the stop; see this run's local serial.log")
            # Interactive runs are diagnostics driven by a person at the keyboard: stopping before the
            # verification is the user's choice, so they keep the old behaviour.
            stopped_by_boot_count = record.get("boots_observed") is not None
            if mode is not RunMode.INTERACTIVE:
                # A boot-count stop is a measurement of the failure cycle, not a successful boot.
                # The verification must belong to the cycle the snapshot is taken from: setup seen
                # three resets ago says nothing about the guest that is running now, and protocol
                # inspection alone would accept it.
                verified, wanted, detail = final_cycle_verdict(mode, capture, navigation_offset, marker)
                boots = detail["final_cycle"]
                record["final_cycle_evidence"] = detail

                # A reset whose banner never arrived leaves the capture short one cycle: the
                # verification on record belongs to the cycle before it, and the snapshot is of a
                # guest that has already been reset. QEMU logs every reset, so the two counts can be
                # compared even when the guest's output stopped mid-line.
                resets_logged = 0
                if local_path(directory / "qemu.log").exists():
                    resets_logged = local_path(directory / "qemu.log").read_text(
                        errors="replace").count("Watchdog timer expired.")
                incomplete = resets_logged >= boots
                if incomplete:
                    record["incomplete_final_cycle"] = True
                    verified = False
                apply_verdict(record, mode, detail, verified)
                if not verified:
                    reason = (f"stopped after {stop_after_boots} reboot cycle(s)" if stopped_by_boot_count
                              else "stopped before the selected verification completed")
                    raise RuntimeError(
                        f"{reason} without reaching {wanted} in the final cycle; "
                        "the checkpoints in this record are the measurement")
            if mode is RunMode.PXE_TEST:
                try:
                    record["pxe_network"] = verify_capture(
                        local_path(directory / "packets.pcap").read_bytes(), payload.read_bytes(),
                        payload.name)
                except (RuntimeError, ValueError) as error:
                    # The exact-transfer check failed. A supplied image that
                    # printed its marker proves execution but not the transfer,
                    # so it is only accepted when the caller opts in; the record
                    # then says so, and the raw capture is kept for inspection.
                    if pxe_image is None or marker is None:
                        raise
                    if not allow_marker_only:
                        raise RuntimeError(
                            f"packet proof failed ({error}); a marker alone is weaker "
                            "evidence than the exact-transfer check. Pass "
                            "--pxe-allow-marker-only to accept it for this run")
                    record["pxe_network_error"] = str(error)
                    record["pxe_packet_proof"] = (
                        "marker-only override used: the supplied image printed its "
                        "marker, but its exact transfer was not verified")
                    record["pxe_execution_verified"] = True
                    marker_only = True
                if pxe_image is not None:
                    record["pxe_network_note"] = "delivered image supplied by the caller"
            tables = re.findall(r"^ACPI (\w{4}) ([0-9a-f]{8}) ([0-9a-f]{12})$",
                                capture.decode("utf-8", "replace"), re.M)
            if tables:
                record["acpi_tables"] = [
                    {"signature": signature, "length": int(length, 16), "address": int(address, 16)}
                    for signature, length, address in tables]
            if record.get("acpi_tables"):
                # The guest printed where its ACPI tables live; dump them while it is still stopped.
                # They stay in this run's directory (ignored), never in the repository.
                captured = []
                for entry in record["acpi_tables"]:
                    target = local_path(directory / f"acpi-{entry['signature']}-{entry['address']:#x}.bin")
                    qmp.request("pmemsave", {"val": entry["address"], "size": entry["length"],
                                             "filename": str(target)})
                    entry["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
                    captured.append(target.name)
                record["acpi_files"] = captured
            snapshot = local_path(directory / "dxe-memory.bin")
            qmp.request("pmemsave", {"val": 0x84000000, "size": 0x4000000,
                                     "filename": str(snapshot)})
            record.update(protocol_table_inspection(
                capture, lambda: inspect_protocols(snapshot.read_bytes(), 0x84000000)))
            table = record.get("architectural_protocols")
            if table and not all(entry["present"] for entry in table["protocols"]):
                raise RuntimeError("setup reached with missing architectural protocols")
            qmp.request("pmemsave", {"val": nor_live_base(nor_size), "size": nor_size,
                                     "filename": str(partial)})
            if partial.stat().st_size != nor_size:
                raise RuntimeError("QEMU returned a truncated NOR snapshot")
            partial.chmod(0o600)
            partial.replace(state)
            record["state_sha256_after"] = hashlib.sha256(state.read_bytes()).hexdigest()
            record["result"] = result_value(mode, marker_only)
        except BaseException as error:
            record["error"] = str(error)
            # A failed run is exactly when its logs matter; carry the path out of
            # run() so the caller can print it for both outcomes.
            setattr(error, "spark_run_directory", directory)
            raise
        finally:
            # A run that never reaches setup still has a flash worth keeping: a
            # reset during a program leaves the pages it committed behind, and
            # that partial state is the evidence. Snapshot it beside the run,
            # never over the caller's image.
            if qmp is not None and process is not None and process.poll() is None:
                try:
                    qmp.request("stop")
                    leftover = local_path(directory / "nor-after.bin")
                    qmp.request("pmemsave", {"val": nor_live_base(nor_size), "size": nor_size,
                                             "filename": str(leftover)})
                    record["nor_after_sha256"] = hashlib.sha256(
                        leftover.read_bytes()).hexdigest()
                except Exception as error:
                    record["nor_after_error"] = str(error)
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            partial.unlink(missing_ok=True)
            record["checkpoints"] = re.findall(rb"Checkpoint ([0-9A-F]+)", capture)
            record["checkpoints"] = [x.decode() for x in record["checkpoints"]]
            if trace:
                # The diagnostic build prints one line per answered request; record how many
                # this boot produced so the run itself carries the measurement.
                # Both shapes answer the guest: an allowed request logs "FLASH cmd=" and a denied
                # one "FLASH DENY cmd=". Counting only the first made a run that answered ten
                # denials record zero answered requests.
                allowed = capture.count(b"FLASH cmd=")
                denied = capture.count(b"FLASH DENY cmd=")
                record["flash_requests"] = allowed + denied
                record["flash_allowed"] = allowed
                record["flash_denied"] = denied
                record["smc_calls"] = capture.count(b"SMC fid=")
            if trace_pc:
                # A capped observation is censored evidence: record the counts so a reader can tell
                # a complete trace from a truncated one without the command line.
                text = capture.decode("utf-8", "replace")
                summary = re.findall(r"TRACE summary pc=0x[0-9a-f]+ hits=(\d+) printed=(\d+) "
                                     r"limit=(\d+) censored=(\d)((?: cpu\d+=\d+)*)", text)
                checkpoints = re.findall(r"TRACE checkpoint hits=(\d+) printed=(\d+)", text)
                capped = re.findall(r"TRACE capped hits=(\d+) printed=(\d+) limit=(\d+)", text)
                if summary:
                    hits, printed, _limit, censored, per_cpu = summary[-1]
                    record["trace_hits"] = int(hits)
                    record["trace_printed"] = int(printed)
                    record["trace_censored"] = bool(int(censored))
                    record["trace_source"] = "summary"
                    if per_cpu.strip():
                        record["trace_per_cpu"] = {
                            key: int(value) for key, value in
                            re.findall(r"(cpu\d+)=(\d+)", per_cpu)}
                elif capped:
                    hits, printed, _limit = capped[-1]
                    record["trace_hits"] = int(hits)
                    record["trace_printed"] = int(printed)
                    record["trace_censored"] = True
                    record["trace_source"] = "cap line; the guest did not shut down cleanly"
                elif checkpoints:
                    hits, printed = checkpoints[-1]
                    record["trace_hits"] = int(hits)
                    record["trace_printed"] = int(printed)
                    record["trace_censored"] = True
                    record["trace_source"] = "last checkpoint; the guest did not shut down cleanly"
                else:
                    # A run cut off before its summary or cap still leaves hit lines behind; they are
                    # the only count there is, and the observation is censored by definition.
                    hits = re.findall(r"TRACE hit \d+ cpu=(\d+)", text)
                    if hits:
                        record["trace_hits"] = len(hits)
                        record["trace_printed"] = len(hits)
                        record["trace_censored"] = True
                        record["trace_source"] = "hit lines; the guest did not shut down cleanly"
                        per_cpu = {}
                        for cpu in hits:
                            key = f"cpu{cpu}"
                            per_cpu[key] = per_cpu.get(key, 0) + 1
                        record["trace_per_cpu"] = per_cpu
                    else:
                        record["trace_error"] = "no plugin output; the trace is unmeasured"
            # A reboot is not a cause. The cpu_reset category names one, and the watchdog is what
            # resets the board-faithful image, so record it rather than leaving it in a log.
            log = local_path(directory / "qemu.log")
            if log.exists():
                text = log.read_text(errors="replace")
                record["watchdog_resets"] = text.count("Watchdog timer expired.")
                record["reset_note"] = ("the GWDT reset the machine"
                                        if record["watchdog_resets"] else
                                        "no watchdog reset logged")
            local_path(directory / "run.json").write_text(json.dumps(record, indent=2) + "\n")
    return directory


def note_verified(record, boots):
    """Record that the selected verification happened in boot cycle `boots`."""
    record.setdefault("verified_boots", [])
    if boots not in record["verified_boots"]:
        record["verified_boots"].append(boots)


def apply_verdict(record, mode, detail, verified):
    """Write the record's verification fields from the same detail the verdict came from.

    Both paths use it — the deadline (evidence already queued) and the stop — so the record cannot
    claim a state the exit status disagrees with, whichever way the run ended.
    """
    if mode is not RunMode.PXE_TEST:
        last_setup = detail.get("setup_at", -1)
        record["setup_visible"] = last_setup >= 0
        if last_setup >= 0:
            record["setup_boot"] = detail["setup_cycle"]
    if mode is RunMode.SETUP_TEST and detail.get("navigation_at", -1) >= 0:
        record["keyboard_navigation_verified"] = True
        record["keyboard_boot"] = detail["navigation_cycle"]
        note_verified(record, detail["navigation_cycle"])
    if mode is RunMode.PXE_TEST and detail.get("pxe_at", -1) >= 0:
        record["pxe_execution_verified"] = True
        record["pxe_boot"] = detail["pxe_cycle"]
        note_verified(record, detail["pxe_cycle"])
    record["final_cycle_verified"] = verified


def final_cycle_verdict(mode, capture, navigation_offset, marker):
    """Whether the cycle the guest is in now carries the verification the mode asks for.

    Read from the capture alone so the same rule decides the verdict after a stop and when the clock
    runs out: the *last* setup screen, navigation line or marker must belong to the final cycle; a
    marker that appears in every cycle therefore certifies only the cycle it was last seen in.
    """
    boots = capture.count(b"MON: boot STMM")

    def cycle_of(position):
        return capture.count(b"MON: boot STMM", 0, position)

    detail = {"final_cycle": boots}
    if mode is RunMode.PXE_TEST:
        last = capture.rfind(marker) if marker else -1
        detail.update(pxe_at=last, pxe_cycle=cycle_of(last) if last >= 0 else None)
        return (last >= 0 and cycle_of(last) == boots), "PXE execution", detail
    last_setup = capture.rfind(b"Aptio Setup - AMI")
    detail.update(setup_at=last_setup, setup_cycle=cycle_of(last_setup) if last_setup >= 0 else None)
    verified = last_setup >= 0 and cycle_of(last_setup) == boots
    if mode is RunMode.SETUP_TEST:
        last_nav = (capture.rfind(b"Power On Behavior", navigation_offset)
                    if navigation_offset is not None else -1)
        detail.update(navigation_at=last_nav, navigation_cycle=cycle_of(last_nav) if last_nav >= 0 else None)
        return verified and last_nav >= 0 and cycle_of(last_nav) == boots, "setup and navigation", detail
    return verified, "setup", detail


# What a payload prints after it has taken the machine over. The DXE core's own image, including the
# architectural-protocol table the inspection looks for, is boot-services memory: once the payload
# calls ExitBootServices that memory is the guest's to reuse, so a table that is missing afterwards
# says nothing about the firmware. Measured on 245 stored runs: the ten runs whose table could not be
# found all reached `Run /init as init process`, while four runs with the same marker still found it
# intact — survival of the region is luck, which is why a *found* table stays evidence and a missing
# one is not.
HANDOFF_MARKERS = (b"EFI stub:", b"Run /init as init process")


def guest_left_boot_services(capture):
    """The payload's own evidence that firmware memory has stopped being the firmware's."""
    return [marker.decode() for marker in HANDOFF_MARKERS if marker in capture]


def protocol_table_inspection(capture, inspect):
    """The table an inspection found, or why this run cannot observe one.

    `inspect` is called only as the record's evidence, and a failure is re-raised unless the payload
    demonstrably left boot services. The strict reading is kept wherever it can be taken: a run that
    still holds the table is checked exactly as before.
    """
    try:
        return {"architectural_protocols": inspect()}
    except ValueError as error:
        evidence = guest_left_boot_services(capture)
        if "found 0" not in str(error) or not evidence:
            raise
        return {"architectural_protocols_source":
                f"not observable: {error}; the payload left boot services "
                f"({', '.join(evidence)}), so the DXE image is guest memory again"}


def nor_live_base(nor_size: int) -> int:
    """Where the guest's live flash lives: one seed window and one state page above the loader blob.

    The harness dumps from here, because the loader's own window is restored on every machine reset
    and would report the image the run started from rather than the flash it left behind.
    """
    return 0xa0000000 + nor_size + 0x1000


def epoch_seconds(rtc_base):
    """The epoch a QEMU `-rtc base=` string names, for the monitor's own RTC service.

    QEMU takes `YYYY-MM-DD` or `YYYY-MM-DDTHH:MM:SS`; a value it cannot parse is passed to QEMU
    unchanged and simply not pinned in the monitor, rather than guessed at here.
    """
    if not rtc_base:
        return None
    for form in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return int(datetime.strptime(rtc_base, form).replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            continue
    return None


def parse_megabytes(text: str) -> int:
    units = {"K": 1 / 1024, "M": 1, "G": 1024, "T": 1024 * 1024}
    suffix = text[-1].upper()
    value = float(text[:-1]) if suffix in units else float(text)
    return int(value * units.get(suffix, 1))


def available_megabytes(cgroup_root=Path("/sys/fs/cgroup"), host_megabytes=None):
    """Memory a boot may use: host `MemAvailable`, or the cgroup's headroom if that is smaller.

    Reading only `/proc/meminfo` is not enough. Inside a container the kernel's OOM killer applies the
    cgroup's own limit, and a guest that the host could fit is killed with SIGKILL the moment the
    group reaches it — which reads as a mysterious exit 137, not as a refusal. This host is a cgroup
    with a 16 GiB cap, and a 3 GiB guest was killed that way with 7.6 GiB reported free.

    `host_megabytes` replaces the `/proc/meminfo` reading. The rule this function implements is
    "the smaller of the two", and the host's own number changes between any two calls, so a test that
    wants to check the rule supplies it instead of reading it twice and comparing.
    """
    host = host_megabytes
    if host is None:
        try:
            with open("/proc/meminfo", encoding="ascii") as handle:
                for line in handle:
                    if line.startswith("MemAvailable:"):
                        host = int(line.split()[1]) // 1024
                        break
        except OSError:
            host = None

    limit = usage = None
    # cgroup v2 first, then v1, both at the root the caller named so a test can point elsewhere.
    for limit_name, usage_name in (("memory.max", "memory.current"),
                                   ("memory/memory.limit_in_bytes", "memory/memory.usage_in_bytes")):
        try:
            limit_text = (cgroup_root / limit_name).read_text().strip()
            usage_text = (cgroup_root / usage_name).read_text().strip()
        except OSError:
            continue
        if limit_text == "max":
            continue
        limit = int(limit_text) // (1024 * 1024)
        usage = int(usage_text) // (1024 * 1024)
        break
    # A limit of ~2**63 means "unlimited" on a v1 hierarchy.
    if limit is not None and limit < 1 << 40:
        headroom = max(limit - (usage or 0), 0)
        return headroom if host is None else min(host, headroom)
    return host


def stage_foreign(source: Path) -> Path:
    """Validate a vendor readback once and return an immutable-sourced working copy."""
    from tools.nor_image import ImageError, inspect, stage

    try:
        report = inspect(source)
    except ImageError as error:
        raise ValueError(f"foreign image rejected: {error}") from None
    staged = ROOT / "firmware" / "state" / f"staged-{report.sha256[:12]}.bin"
    if staged.exists():
        if hashlib.sha256(staged.read_bytes()).hexdigest() != report.sha256:
            raise ValueError(f"existing staged copy {staged} does not match the source")
        return staged
    source_sha, staged_sha = stage(source, staged)
    if source_sha != staged_sha:
        raise ValueError("staged copy does not match the source")
    print(f"staged {source} ({source_sha[:16]}) -> {staged}")
    return staged


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", default=str(ROOT / "build/qemu-spark/qemu-system-aarch64"))
    parser.set_defaults(mode=RunMode.INTERACTIVE)
    checks = parser.add_mutually_exclusive_group()
    checks.add_argument("--smoke-test", dest="mode", action="store_const", const=RunMode.SETUP_TEST,
                        help="verify setup and keyboard navigation, then save and stop")
    checks.add_argument("--pxe-test", dest="mode", action="store_const", const=RunMode.PXE_TEST,
                        help="verify native IPv4 PXE, exact TFTP download and EFI execution")
    parser.add_argument("--timeout", type=int, default=45, help="boot deadline in seconds")
    parser.add_argument("--state-file", type=Path, default=ROOT / "firmware/state/spark-nor.bin")
    parser.add_argument("--foreign-nor", type=Path,
                        help="boot from a vendor 32 MiB readback; a validated copy is staged "
                             "and the original is never written")
    parser.add_argument("--firmware-version", default=DEFAULT_VERSION, choices=sorted(VERSIONS),
                        help="SoC release to boot; the board ran 2.155.11, the reproduction 2.158.15")
    parser.add_argument("--erase-us", type=int,
                        help="sector erase latency in microseconds; defaults to --write-us")
    parser.add_argument("--memory",
                        help="guest RAM to allocate, at least 2G: the model's loader tag ends at "
                             "0xC0000000, so less RAM cannot be described. The profile aliases PCI BAR "
                             "space over the first gigabyte, so the guest sees (size - 1 GiB) starting "
                             "at 0x80000000; run.json records memory_usable alongside memory")
    parser.add_argument("--smp", type=int, help="guest CPU count; the board reports 20")
    parser.add_argument("--rtc-base", help="pin the guest RTC, e.g. 2026-01-01T00:00:00")
    parser.add_argument("--seed", type=int, help="pin QEMU's random seed")
    parser.add_argument("--pxe-marker",
                        help="success marker printed by a supplied --pxe-image; without it a "
                             "supplied image is only delivered, never confirmed executed")
    parser.add_argument("--pxe-allow-marker-only", action="store_true",
                        help="accept a supplied image proved only by its printed marker when "
                             "the packet proof fails; the default is to fail the run")
    parser.add_argument("--pxe-image", type=Path,
                        help="serve this EFI application over the PXE path instead of the "
                             "built-in probe; requires --pxe-test and --pxe-marker")
    parser.add_argument("--gdb", type=int, metavar="PORT",
                        help="diagnostic: hold the CPU at reset and expose a GDB stub on PORT")
    parser.add_argument("--board", action="store_true",
                        help="board-faithful defaults: a whole-device flash image of the board's "
                             "64 MiB part, the latch armed, and the boot image taken from the "
                             "image at --code-offset (default 0x2000000)")
    parser.add_argument("--flash-layout", choices=("auto", "whole", "store"), default="auto",
                        help="which layout the state is: auto reads its size (64 MiB is the board's "
                             "whole device, 32 MiB a store), whole is offset 0 with the code volume "
                             "at 0x2000000, store is the legacy 0x03000000 window")
    parser.add_argument("--program-model", choices=("page", "byte"), default=None,
                        help="how a program pulse is modelled: page commits the request then spends "
                             "the page budget, byte spreads the budget so a reset leaves a prefix "
                             "written. Defaults to byte for the whole-device layout, page for a store")
    parser.add_argument("--flash-image", type=Path,
                        help="boot from a whole-device flash image: the boot image is read "
                             "from it and the guest sees the same bytes as flash, so a write "
                             "to the code region is visible to the next boot")
    parser.add_argument("--code-offset", type=lambda v: int(v, 0), default=None,
                        help="offset of the boot image inside --flash-image (default 0, or "
                             "0x2000000 under --board); zero here means the start of the device")
    parser.add_argument("--nor-size", type=lambda v: int(v, 0), default=None,
                        help="modelled flash size; defaults to the state file's own size, "
                             "or 0x2000000 when there is no state file yet")
    parser.add_argument("--write-us", type=int, default=None,
                        help="modeled program latency in microseconds (0 disables it); "
                             "--board supplies a datasheet-typical value when this is omitted")
    parser.add_argument("--stop-after-boots", type=int, default=None,
                        help="stop after this many reboot cycles instead of waiting out --timeout; a "
                             "class count taken this way does not depend on host speed")
    parser.add_argument("--gwdt-timeout-ms", type=int, default=None,
                        help="pre-arm the GWDT with this timeout in the monitor, as a BL31 would")
    parser.add_argument("--accept-mmap-notify", action="store_true",
                        help="experimental: answer 0xc4000122 with success instead of NOT_SUPPORTED")
    parser.add_argument("--nvme-image", type=Path, metavar="PATH", action="append",
                        help="attach a raw NVMe namespace from this image; repeat for a second "
                             "namespace (media and target disk). The boot writes to a per-run "
                             "snapshot, never to the file")
    parser.add_argument("--extended-ram", action="store_true",
                        help="describe RAM up to the allocation even past 4 GiB; off by default "
                             "because the machine's high MMIO windows are not modelled")
    parser.add_argument("--trace-pc", type=lambda v: int(v, 0), metavar="ADDRESS",
                        help="diagnostic: log every execution of this guest address with the TCG "
                             "plugin, and record the counts; a capped trace is marked censored")
    parser.add_argument("--trace-sample", type=int, default=0, metavar="N",
                        help="sample the address of every N-th translated basic block executed; "
                             "answers 'where is the guest running' for a boot that stops printing")
    parser.add_argument("--trace-limit", type=int, default=0, metavar="N",
                        help="stop logging after N hits (0, the default, means no limit)")
    parser.add_argument("--flash-trace", action="store_true",
                        help="build a monitor that logs each answered flash request to the console")
    parser.add_argument("--flash-policy", choices=("armed", "open"), default="armed",
                        help="armed matches the board's armed latch; open models the pre-arming window")
    args = parser.parse_args()
    if not 5 <= args.timeout <= 3600:
        parser.error("timeout must be between 5 and 3600 seconds; a boot that reaches userspace "
                     "under TCG spends minutes of wall time on seconds of kernel time")
    try:
        state = args.state_file
        if args.foreign_nor:
            state = stage_foreign(args.foreign_nor)
        if args.board:
            apply_board_preset(args)
        if args.flash_image is not None:
            if args.foreign_nor:
                raise ValueError("--flash-image and --foreign-nor are different sources")
            state = args.flash_image
            if not state.is_file():
                raise ValueError(f"flash image not found: {state}")
            # The same resolver as a state file, so `--board --flash-layout store` is refused for
            # this source too instead of slipping through the other branch.
            layout, _size, nor_offset, code_offset = resolve_flash_layout(
                state.stat().st_size, args.flash_layout, args.board, None, None)
            if layout != "whole" or nor_offset != 0:
                raise ValueError("--flash-image is a whole-device source; it cannot be booted as "
                                 "the store-only layout")
            # An explicit --nor-size (or --board's preset) is checked against the file rather than
            # overwritten by it: the image's length is what the part is, so a request for another
            # size is a contradiction, and booting the file at the requested size would silently
            # slice a device image down to a window it is not.
            if args.nor_size is not None and args.nor_size != state.stat().st_size:
                raise ValueError(f"{state} is {state.stat().st_size:#x} bytes, "
                                 f"not the modelled {args.nor_size:#x}")
            args.nor_size = state.stat().st_size
            # Deliberate and recorded: an explicit image that omits --code-offset keeps the old
            # contract, in which the boot image starts at offset zero of the file, rather than
            # taking the whole-device default. A state file that resolves to the whole-device layout
            # does use the default. Changing either would reinterpret existing receipts.
            code_offset = 0 if args.code_offset is None else args.code_offset
            code_offset_source = ("explicit" if args.code_offset is not None
                                  else "zero-for-explicit-image")
        else:
            size = state.stat().st_size if state is not None and state.exists() else None
            layout, nor_size, nor_offset, code_offset = resolve_flash_layout(
                size, args.flash_layout, args.board, args.code_offset, args.nor_size)
            code_offset_source = ("explicit" if args.code_offset is not None else
                                  "whole-device-default" if nor_offset == 0 else "not-sliced")
            if layout == "whole":
                args.nor_size = nor_size
                if size is None:
                    # run() creates it, after preparing the release, at this exact path.
                    pass
                elif size != WHOLE_DEVICE_SIZE:
                    raise ValueError(
                        f"{state} is {size:#x} bytes; a whole device is {WHOLE_DEVICE_SIZE:#x}. "
                        "Pass --flash-layout store to boot it as the store-only layout it is")
            else:
                args.nor_size = nor_size
        args.nor_size = resolve_nor_size(state, args.nor_size)
        if args.flash_image is not None and state.stat().st_size != args.nor_size:
            raise ValueError(
                f"{state} is {state.stat().st_size:#x} bytes, not the modelled {args.nor_size:#x}")
        if args.smp not in (None, 1, 2):
            # Two CPUs are modelled: the profile sizes the redistributor region from the count and
            # parks the secondaries until someone starts them. Their execution is not modelled yet,
            # which the record says in `secondary_cpus`.
            raise ValueError("the spark-fw-lab machine models one or two CPUs")
        if args.memory is not None:
            free = available_megabytes()
            wanted = parse_megabytes(args.memory)
            if wanted < 2048:
                # The loader tag's fixed regions (monitor, MM image, communication pages, 64 MiB NOR)
                # end at 0xC0000000, so the tag cannot describe less RAM than that. A smaller
                # allocation would leave the guest with pointers into memory QEMU never mapped.
                raise ValueError(f"--memory {args.memory} is smaller than the model's fixed layout: "
                                 "the loader tag's regions end at 0xC0000000, so at least 2G is needed")
            if free is not None and wanted + 512 > free:
                raise ValueError(f"--memory {args.memory} needs about {wanted + 512} MiB but only "
                                 f"{free} MiB is available on this host; the board's 128 GB class "
                                 "cannot be modelled here")
        if args.pxe_image is not None and args.mode is not RunMode.PXE_TEST:
            raise ValueError("--pxe-image requires the PXE test run mode")
        if args.pxe_marker and args.pxe_image is None:
            raise ValueError("--pxe-marker needs --pxe-image: the built-in probe prints its own")
        if args.pxe_image is not None and not args.pxe_marker:
            raise ValueError(
                "--pxe-image needs --pxe-marker: without a string the supplied "
                "image can only be proved delivered, never confirmed executed, and "
                "the run would wait for a timeout it cannot pass")
        # Keyword arguments deliberately: this call had sixteen positional slots, and a
        # parameter added in the middle silently shifted every one after it.
        directory = run(qemu=args.qemu, mode=args.mode, timeout=args.timeout, state_path=state,
                        policy=args.flash_policy, write_us=args.write_us or 0,
                        erase_us=args.erase_us, pxe_image=args.pxe_image,
                        pxe_marker=args.pxe_marker, rtc_base=args.rtc_base, seed=args.seed,
                        firmware_version=args.firmware_version, memory=args.memory,
                        smp=args.smp, nor_size=args.nor_size, gdb_port=args.gdb,
                        allow_marker_only=args.pxe_allow_marker_only, nor_offset=nor_offset,
                        code_offset=code_offset, trace=args.flash_trace,
                        code_offset_source=code_offset_source,
                        program_model=args.program_model, trace_pc=args.trace_pc,
                        trace_limit=args.trace_limit, trace_sample=args.trace_sample,
                        extended_ram=args.extended_ram,
                        nvme_images=args.nvme_image,
                        accept_mmap_notify=args.accept_mmap_notify,
                        gwdt_timeout_ms=args.gwdt_timeout_ms,
                        stop_after_boots=args.stop_after_boots)
        if args.mode is RunMode.PXE_TEST:
            record = json.loads((directory / "run.json").read_text())
            verified = record.get("pxe_packet_proof", "").startswith("marker-only")
            result = ("Spark PXE download and EFI execution verified" if not verified
                      else "Spark PXE: marker seen, exact transfer NOT verified"
                      " (--pxe-allow-marker-only)")
        else:
            result = "Spark UEFI session verified"
        print(f"\n{result}. Local logs: {directory}")
    except (OSError, ValueError, RuntimeError, TimeoutError, subprocess.SubprocessError) as error:
        if os.environ.get("SPARK_DEBUG"):
            raise                      # a one-line message hides where the failure came from
        print(f"error: {error}", file=sys.stderr)
        directory = getattr(error, "spark_run_directory", None)
        if directory is not None:
            print(f"Local logs: {directory}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted before a verified state snapshot.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
