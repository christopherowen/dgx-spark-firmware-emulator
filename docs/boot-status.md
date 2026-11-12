# Measured Spark boot status

Verified locally on 2026-10-31 with QEMU 11.1.2 TCG and one AArch64 CPU:
**the real Spark UEFI boots through BDS to interactive AMI setup.** Keyboard
navigation from Main to Advanced works. Native IPv4 PXE obtains a DHCP lease,
downloads an original EFI application and executes it. A second cold boot
reuses the saved variable store and passes the same checks.

## Inputs and execution

Official LVFS release **152274**, SoC **2.158.15**, supplies both unchanged
vendor images. `tools/spark_image.py` pins and verifies the cabinet, capsule
and 32 MiB UEFI hashes. `tools/prepare_mm.py` extracts the SPKG v2 Standalone MM
package and verifies SHA-256
`591d5b33bad81bed6ec402dd2a5d20ed37bd3920d261420e0c4e3ba09d9431fa`.
No vendor images, captures or disassembly are published.

The original lab monitor starts at `0x80020000` in EL3. It boots the real MM
package at `0x93a40000` in S-EL1, receives its initialization-complete wait, and
starts UEFI at `0x80800000` in NS-EL2. It routes actual MM communications rather
than installing a substitute variable protocol. The real MM driver formats
and operates on the emulated NOR variable partition. A modeled PMIC RTC
service supports the real UEFI clock driver.

The synthetic loader memory tag at `0x80010000` has size `0x1868`, plus its
terminator, because SEC copies the complete tag into a GUID HOB. It reserves
live monitor, MM, communication/page-table and NOR backing ranges. This is an
inferred laboratory entry contract. No preceding boot ROM, vendor BL31 or
production partition manager is executed. Hash checks do not validate the
capsule PKCS#7 trust chain or establish authenticated boot.

## Verified results

| Check | Result |
| --- | --- |
| Unchanged SEC and DXE core | Executed; the separate stock-QEMU diagnostic also verifies exact entry breakpoints and single-steps |
| Real Standalone MM firmware | Initializes, waits and processes real UEFI variable requests |
| NOR-backed variable services | Read/program/erase operate on local persistent emulated flash |
| All 13 DXE architectural protocols | Present; complete ordered GUID table and terminator validated in paused guest memory |
| BDS and console initialization | Completes; serial checkpoints include `AD`, `A9`, `AB` |
| AMI setup | Interactive Main screen, real clock advancing |
| Keyboard input | Right-arrow opens Advanced and its settings list |
| Subsequent cold boot | Existing NOR state loaded; setup, navigation and all protocols verified again |
| Native IPv4 PXE | Bundled Realtek UNDI/SNP/DHCP/PXE drivers obtain a lease and download an EFI program over TFTP |
| Downloaded EFI execution | Original probe prints a fresh per-run token; complete acknowledged transfer matches the probe bytes |
| Vendor Linux, under the same profile | The recovery kernel and installer initramfs reach userspace: `Run /init as init process`, the installer banner, `VERSION="1.155.49"` (`docs/linux-under-the-profile.md`, `tests/test_profile_userspace.py`) |
| Installed Linux from the installer-produced NVMe image | Kernel `7.0.0-1019-nvidia`, 4 KiB pages; default `graphical.target` transaction reaches `multi-user.target` and serial login with no `systemd.unit=` override (2.158.15) |
| External fan module via FF-A packet model | Explicit lab identity variant loads; CLI state `0 -> 3 -> 0` reads back, with model-computed RPM `1260/1890 -> 4500/4500 -> 1260/1890` (2.155.11; no native ACPI fan declared) |
| External fan module via native ACPI | Lab identity variant selects already-bound `acpi-fan` for both `PNP0C0B` devices, follows their thermal cooling links, and registers no packet floor device; CLI correctly fails because that device is absent (2.158.15) |
| Module signing and shim MOK enrollment | Shim consent survives cold boot. The Secure-Boot-off store reproduces `4096 → 12288 (OE)`; separately provisioned lab PK/KEK/db enable Secure Boot and kernel MOK trust, with the same signed fan module loading at `4096 → 4096 (O)` |
| GPU and physical NIC firmware | Not part of this result |

The 13 protocols are Security, Cpu, Metronome, Timer, Bds, WatchdogTimer,
Runtime, Variable, VariableWrite, Capsule, MonotonicCounter, Reset and
RealTimeClock. A translated code trace or a printed checkpoint alone is not
accepted as a full-boot result.

## Reproduce

```sh
python3 tools/build_qemu.py
python3 tools/boot_spark.py --smoke-test
python3 tools/boot_spark.py --smoke-test
python3 tools/boot_spark.py --pxe-test --state-file firmware/state/pxe-nor.bin
python3 tools/boot_spark.py
```

The smoke test sends Escape at the firmware's setup prompt, waits for the
actual setup title, sends a right-arrow and checks the Advanced settings.
It then stops the CPU, inspects the architectural-protocol table and saves
NOR state atomically. A firmware exception or missed boot deadline fails the
run. Interactive mode accepts serial keyboard input; Ctrl-C saves and exits.

Evidence stays under ignored `firmware/runs/<UTC timestamp>/`: `serial.log`,
`qemu.log`, the paused DXE snapshot, generated handoff, hashes and `run.json`.
The record links each run's input/output NOR hashes, allowing persistence to
be checked without publishing any generated variable-store contents. Failed
runs preserve the previous NOR file. Concurrent runs cannot share one store.

Older receipts record `mode: interactive` but not how host input reached the
guest. That field alone cannot distinguish working serial-stdin input from
QMP keyboard events, which do not reach this profile's UART. Current receipts
record `console_input_transport: serial-stdin`; scheduled script attempts also
record `console_script.transport`. These identify the host transport, not
guest execution: the guest's command output is still required as evidence.

## PXE evidence

The PXE test does not enter setup. Its optional RTL8168C compatibility NIC is
attached through the lab PCI bridge. SMCCC TRNG supplies host-backed entropy
to the real RngDxe; EFI RNG is a dependency of the firmware's network drivers.
No additional EFI network driver, iPXE or injected network protocol is used.
The monitor loads only UEFI/MM; the probe enters guest memory through the
native firmware's network download and EFI loader.

The initial 2026-10-28 default-settings run used client `10.0.2.15`, server `10.0.2.2`,
and a 2,048-byte original probe transferred in two TFTP data blocks, with a
negotiated block size of 1,468 bytes. The runner checks DHCP DORA for the NIC's
MAC and transaction ID, reconstructs the exact payload from acknowledged TFTP
blocks, and requires its fresh execution token. Each run uses a newly built
probe and records its hash and transfer results in `run.json`. Both a fresh
NOR store and a subsequent cold boot are supported.

QEMU libslirp provides a restricted local test network. Its dedicated TFTP
directory contains only the generated original probe; vendor images, state,
objects and logs are never served. The PCAP remains in the ignored run folder.
This establishes IPv4 PXE and EFI application execution. IPv6 PXE, HTTP boot
and the physical NIC's embedded firmware have not been verified. Installed
Linux boot and lab-key Secure Boot image acceptance are separate results below;
the PXE result does not establish Secure-Boot-on network boot.

## Model boundaries

The [QEMU profile](../qemu/README.md) provides functional GIC/timers/UART and
QEMU xHCI cores, plus bounded configuration models for GPIO, EINT, USB PHY,
PCIe and board information. The original [monitor](../monitor/README.md)
implements the observed FF-A compatibility subset, flash/clock peers and
host-backed SMCCC TRNG.
The [NS memory-sharing extension](mm-sharing.md) adds a bounded FF-A lifecycle
without changing STMM's fixed MM communication-buffer checks. A separate
local probe verifies the unchanged firmware accepts its advertised buffer and
rejects an outside pointer even after a successful share.
Root of Trust requests receive explicit nonzero errors by default. The
installed-Linux and Secure Boot runs explicitly use `--rot-functional`,
whose success replies **authenticate nothing**. No preceding firmware
authentication, attestation or production root of trust is established.

Only the optional compatibility NIC is connected through the minimal PCIe
bridge; most roots remain empty. USB PHY register readback and clock
status approximate initialization, without electrical behavior. Loader-cached
board/version data and most fuse fields use synthetic defaults. Setup's
SMBIOS total-memory field currently shows 0 GB even though UEFI runs in QEMU
RAM; this display is not a physical memory inventory. Physical ACPI reports
GICv4 and 20 CPUs; the lab uses GICv3 and one CPU. The inventoried hardware ran
SoC 2.155.11, a different revision from the emulated update.

There is no full GB10/MT8901 implementation, GPU, physical-device passthrough,
secure stage-2 isolation, production secure boot, firmware flashing, or
migration support. Watchdog resets (`--gwdt-timeout-ms`, `--stop-after-boots`)
preserve live flash state and code edits across reboot cycles within a run;
otherwise use a fresh QEMU process per cold boot. Reaching setup establishes
UEFI boot, not execution of every component in the SoC image.

The older `run_spark.py` diagnostic deliberately omits the monitor/MM package.
It remains useful for SEC/DXE entry checks and explicit failure scenarios;
its continuations are not the full-boot runner.

## Installed-disk stall diagnosis and correction, 2026-11-11

The vendor installer completes against the profile and the produced disk boots under the vendor UEFI to
its own kernel (`7.0.0-1019-nvidia`), through `firmware -> EFI/BOOT/BOOTAA64.EFI -> shim -> GRUB -> /boot`
without an NVRAM entry. Before the monitor fix, output stopped after `scmi_core`.

The earlier claim that the CPU was waiting for an interrupt was wrong. Zero new translated-block samples
in ten minutes rules out a retiring loop, but not recursive faults fetching an unmapped vector. The
decisive `--trace-exceptions` run (`20261111T154523.547381Z`) records the **first** fault:

```text
STMM resume PC=0x93a7e168
EL1 -> EL1 instruction abort: ESR=0x8600000f, FAR=ELR=0x93a7e168
vector PC=0x200; subsequent ESR=0x86000005, FAR=ELR=0x200
```

The first fault is execute permission at level 3; the later faults are level-1 translation faults at the
zero-based vector. The register file from `20261111T155336.769648Z-context-before` explicitly says
**`S EL1h`**: this is secure STMM, not the non-secure Linux kernel. Its vector base was already zero
at the first secure save, before UEFI ran. The monitor faithfully restores that zero; it did not zero
Linux's vector base.

The same capture measures both `TCR2_EL1` and `TCR2_EL2` initially `0`, then `2` (`PIE=1`) in the
NS request after `scmi_core`, and still `2` after restoring STMM. These unbanked controls were omitted
from the world context. Linux's permission-indirection state therefore reinterpreted secure legacy
page descriptors and denied the correct return address execute permission.

The monitor now preserves optional TCR2, stage-1 PIR/PIRE0 and HCRX state per world while leaving SCR
feature enables unchanged. A synthetic executable-mapping regression fails with the old monitor and
passes with the fix; NS permission state survives two round trips. Full measurements, hashes and run
identifiers are recorded in [the Linux boot history](linux-under-the-profile.md).

The firmware ROT retry loop, console model and `update_event_printk` infinite-scan theory remain ruled
out. Neither an unchanged sample count nor an FF-A function ID by itself identifies the security state.

With the fix, `20261111T160507.692000Z-context-fixed` reaches `/init` at guest time 18.422 s,
mounts `nvme0n1p2` at 38.672 s, starts systemd 255.4 at 45.105 s and prints
`Welcome to Ubuntu 24.04.4 LTS!`. The capture confirms secure TCR2/PIR/PIRE0/HCRX values restored to
zero and NS PIE/permission values preserved, rather than disabling Linux's features. This is the
installed kernel and root, not the recovery initramfs.

That bounded capture did not reach a login prompt or desktop: it ended while AppArmor profiles
were loading and a vendor software-removal startup job was still in progress. Neither was diagnosed
as a stall. The later serial-login test below supersedes the login limitation, not the desktop claim.
GCS and other optional context groups, the recovery boot after this change, GPU and physical-device
support remain unverified or outside scope.

## The installed disk's boot stanza

`EFI/ubuntu/grub.cfg` on the installed disk was replaced with an explicit stanza because the
installer's original stops printing at the bootconsole line; the original remains beside it as
`grub.cfg.orig`. The isolated login test retains the installed kernel, initrd and root UUID.
The 2026-11-11 run used `systemd.unit=getty.target`; the follow-on default-target test
removes that override and uses only
`console=ttyS0,115200 earlycon=uart,mmio32,0x16a00000 nokaslr`.
The enabled serial getty, test root-password provisioning and module staging are recorded in
[the installed-root test setup](linux-under-the-profile.md#installed-root-serial-login-and-packet-fan-test-setup-2026-11-11);
these are edits to a throwaway, regenerable installer output, not changes to vendor firmware.

## Installed serial login and packet fan control, 2026-11-11

Run **`20261111T175332.261188Z`** retains one `serial.log` with the real
`localhost login:` prompt, authenticated root shell, enabled/active
`serial-getty@ttyS0`, and a natively built module for
`7.0.0-1019-nvidia` with **4096-byte pages** and matching vermagic.
`--ffa-ec` opts into FF-A discovery for packet UUID
`78b04d80-d21d-4986-8acb-467b60247ac5`, endpoint `0x8003`,
properties `0x0109`, RX/TX ownership and DIRECT_REQ2 packet handling.
The monitor's implemented SMCCC version is exactly 1.2 (`0x00010002`);
PSCI advertises that call independently of the EC option.

The module's own log says `transport=FF-A packet scale=EC-RPM` and verifies
the unset lower clamp on probe. `dgx-fan-control status`, `set-state 3`,
another `status`, `automatic`, and a final `status` succeed. State reads
back `0/12 -> 3/12 -> 0/12`; the module's RPM attributes read
`4500/4500` after the floor write and `1260/1890` after automatic restore.
Monitor packet traces corroborate floor `0x1194 -> 0xffff`. These are
computed emulated readings, not physical tachometer/thermal measurements.

**This run exercises a lab identity variant, not the shipping hardware
DMI gate.** The vendor firmware's unprovisioned defaults report
`NVIDIA / DGX Spark / DGX Spark`, not the measured retail
`NVIDIA / NVIDIA_DGX_Spark / P4242`. The shipping module's real refusal
is retained in `20261111T173830.338200Z`. No vendor defaults were patched:
the type-1/type-2 table at `uefi.fd` offsets `0x70f660`/`0x70f6cc`
needs two extra bytes but has only one before the next file.
`tools/fan-module-lab-identity.patch` changes only a local source copy in
`firmware/state/`, enabled by `DGX_EC_LAB_IDENTITY`; the external shipping
checkout and the pinned UEFI/STMM images remain unchanged.

The full command, source/binary provenance, exact disk test hooks and
limitations are in [the Linux evidence](linux-under-the-profile.md#one-installed-guest-run-reaches-login-and-controls-the-modelled-fan).
The run keeps `fan-evidence.json`, `run.json` and its tested lab module.
That historical run did not establish full multi-user startup, desktop, the
2.158.15 ACPI transport, trusted signing, the shipping DMI gate's success path,
or physical cooling. Follow-on evidence is recorded below.

## Default-target and ACPI follow-on

Run **`20261111T191435.858956Z`** retains the 2.158.15 kernel command line
without `systemd.unit=`, the actual `localhost login:` prompt, and guest
journal line `Reached target multi-user.target - Multi-User System.`.
`MULTI_USER_ACTIVE=active` and `DEFAULT_TARGET=graphical.target` corroborate
the completed natural default transaction. Some hardware/network-dependent
services fail; reaching the target is not a healthy desktop or GPU claim.

The lab module logs `transport=ACPI native acpi-fan selected` for both
`PNP0C0B` platform devices. Their driver links point to `acpi-fan`, and
`thermal_cooling` resolves to two type-`Fan` cooling devices. The packet
floor-device census is `FLOOR_DEVICES=0`; the CLI emits
`ERROR expected one 'dgx_ec_fan_floor' cooling device, found 0` and exits 1.
Native ownership predated `modprobe fan`; no extra module load was required.
See [the exact evidence and limits](linux-under-the-profile.md#default-target-boot-and-native-acpi-selection).

Packet run **`20261111T194757.684450Z`** records floor `0x1194`, rational
duties `387000/7740` and `387000/11610`, and forward estimates/readbacks
4500/4500, then restores `0xffff` and the labelled 14% baseline estimates
1260/1890. The duty-to-estimate arithmetic and fan ranges are recovered;
the inverse set-clamp duty, clipping and unset baseline are explicit model
choices, not a calibrated EC control law or physical RPM measurement.
See [the provenance boundary](linux-under-the-profile.md#duty-derived-packet-estimates-and-evidence-boundaries).

## Signed module and MOK follow-on

The key and signed `.ko` were generated in the installed guest; the
artifact reports signer **`DGX Spark emulator lab signing`**. Actual shim
consent is retained in **`20261111T200749.834546Z`**, followed by cold-boot
run **`20261111T201056.330138Z`** reporting
`/boot/efi/MOK.DER is already enrolled`. The same signed artifact loads and
performs the packet state round-trip.

`SecureBoot disabled` and `Platform is in Setup Mode` were intentional
conditions **for those retained runs**. The kernel's Secure Boot/MOK trust
gate excluded the enrolled key from its trusted keyrings: both `.platform`
and `.machine` were empty. Their `TAINT_BEFORE_TRUSTED=4096`,
`TAINT_AFTER_TRUSTED=12288`, `TRUSTED_MODULE_TAINT=OE` remain expected:
`E` is untrusted signature (8192), `O` is out-of-tree code (4096).
No historical taint was hidden or cleared.
See [the unchanged Secure-Boot-off evidence](linux-under-the-profile.md#signed-module-and-cold-boot-mok-consent-with-secure-boot-off)
and [the separately provisioned lab PK/KEK/db](linux-under-the-profile.md#lab-platform-secure-boot-provisioning).

## Platform Secure Boot and trusted module follow-on

Run **`20261112T022953.618760Z`** uses a separately provisioned lab PK/KEK
and **only Microsoft UEFI CA 2011 in db**. Its `BootCurrent: 0000`
identifies `EFI/BOOT/BOOTAA64.EFI`; unchanged shim verifies Canonical-signed
GRUB and kernel. Guest `mokutil` reports `SecureBoot enabled`, efivarfs
reports SecureBoot 1 / SetupMode 0, and the default transaction reaches
both multi-user and graphical targets plus authenticated serial login.

The existing lab MOK appears in `.platform`. The unchanged signed fan
module loads at **4096 → 4096**, module taint **O only**, and completes
floor `0 → 3 → 0` with sysfs estimates `4500/4500` at state 3 and
`1260/1890` after automatic restoration. Integrity lockdown is active;
the unsigned original is actually refused with `Key was rejected by
service`, leaving taint 4096.

Separate one-byte-corruption controls demonstrate actual rejection by
firmware, shim and GRUB/shim-lock, rather than relying only on an enable
flag or on host signature inspection.
See [the boundary-specific refusal table](linux-under-the-profile.md#independent-negative-controls-for-all-three-image-verification-steps)
and [the complete positive evidence](linux-under-the-profile.md#cold-boot-chain-and-kernel-trust).

Both modes are one flag apart, and either is reproducible with the recorded arguments:

```sh
# Secure Boot off (the earlier policy; unsigned modules load and taint E)
python3 tools/boot_spark.py --firmware-version 2.155.11 --memory 2G --timeout 3600 \
    --state-file firmware/state/spark-secureboot-off.bin \
    --nvme-image firmware/state/install-target-40g.img \
    --rot-functional --no-setup-escape --ffa-ec

# Secure Boot on (PK/KEK/db enrolled; signed module only; taint O only)
python3 tools/boot_spark.py --firmware-version 2.155.11 --memory 2G --timeout 3600 \
    --state-file firmware/state/spark-secureboot-on.bin \
    --nvme-image firmware/state/install-target-40g.img \
    --rot-functional --no-setup-escape --ffa-ec
```

The two images are separate state files, not a toggle inside one: `--state-file` selects which
variable store boots, so the Secure-Boot-on run cannot silently fall back to the off policy.
`firmware/state/spark-secureboot-off.bin` preserves the earlier policy;
`spark-secureboot-on.bin` retains the enrolled policy. Both are local-only.
This is not manufacturer provisioning: PK/KEK are disposable lab keys,
the actual unit's key inventory was not copied, dbx is absent, and the
synthetic loader/functional ROT does not authenticate the vendor firmware.


## Remaining platform limits and evidence boundaries

These are separate from the permission-indirection correction and from a
successful installed-Linux boot.

### ROT request capture is not a reconstructed security service

The captured `0x29` request is `0x00000129 0x90000000`; `0x12` is
`0x00000312 0x90000000 0x00000001 0xfcc40d97`, followed by the same request
with `0xb6db6764` as its last word. The leading count interpretation and a
checksum/digest interpretation of the varying word are **[INFERENCE]**.
`--rot-functional` supplies status 0 and no payload. The only established
reply property is **"status 0 is accepted"** on the observed boot path;
the complete reply shape and security semantics remain **unverified**.
Paired hardware ROT request/reply and pointed-memory captures, plus the
caller's post-reply loads/branches (or an authoritative specification),
would settle this. See [the raw shape and closure requirements](runtime-variables.md#the-request-shape-as-far-as-the-captures-support-it).

Retained `20261111T093421.135412Z/qemu.log` lines 40–41 and 106–117
contain the transport capture (host diagnostics, **not UART lines**);
additional `0x12` calls use third word `2` and last word `0x64a0ea5f`.
The short examples above are not an exhaustive per-boot call count.

### Optional context is not covered by the permission-state regression

The fix and executable regression cover TCR2, stage-1 PIR/PIRE0 and HCRX,
with normal-world permission state preserved over secure round trips.
They do **not** establish preservation of guarded-control-stack state
(`GCSCR`, `GCSCRE0`, `GCSPR`), fine-grained trap registers, `SCTLR2`, or
every optional timer/debug/PMU context group. Those groups have not been
independently exercised or exhaustively audited: **unverified**, not
silently supported. Enabling accesses in `SCR_EL3` is not context
preservation. SVE, SME and pointer authentication are explicitly disabled
by the runner, not implemented by this monitor. This is a single-CPU,
non-concurrent world-switch model, not production isolation.

### Three declared SMMUs, not three modelled SMMUs

The IORT record, [research/smmu-iort.json](../research/smmu-iort.json),
declares three 128 KiB windows: `0x13800000`, `0x13000000` and
`0x14900000`. It supplies neither a PCI root-complex node nor PCI stream-ID
mappings. The profile backs the windows with an inert, read-zero/write-drop
region; the guest discovers the declarations but no functional SMMU.

The earlier phrase "three attempts, each a build and a start" overstated
the retained evidence. There are **two recorded QEMU refusals and a
source-level structural argument**, not three independently retained
failure diagnostics:

1. No primary PCI bus: `SMMU is not attached to any PCI bus!`.
2. Attach all three to the same primary bus: the second refuses with
   `An iommu is already attached to this bus`.
3. Source review, **not a third measured failure**:
   `hw/arm/smmu-common.c:smmu_base_realize()` requires PCI attachment and
   `hw/pci/pci.c:pci_setup_iommu_per_bus()` rejects a second per-bus IOMMU.
   These diagnostics are recorded in the research record and confirmed
   in the local QEMU source; their original startup logs are not retained
   with a named run here. Any distinct third startup diagnostic is
   **unverified**.

Thus this QEMU `arm-smmuv3` cannot represent the three instances as the
firmware declares them **without PCI attachment**. Inventing buses or
stream mappings would change the platform contract. A standalone SMMU
model, or measured device/stream mappings and a corresponding platform
description, would be needed to remove the limitation. Guest-side probe
failures are distinct evidence from these host startup refusals.

The separate guest measurement in retained
`20261112T060832.086295Z/serial.log` reports all three probe failures
(ANSI stripped):

```text
[    2.535931] arm-smmu-v3 arm-smmu-v3.0.auto: no translation support!
[    2.541182] arm-smmu-v3 arm-smmu-v3.1.auto: no translation support!
[    2.542548] arm-smmu-v3 arm-smmu-v3.2.auto: no translation support!
```

### eSPI is not reached

The capsule contains a plaintext `eSPI Service` with a manifest and
controller window, but the runner boots only UEFI and STMM; no exercised
boot path reaches that service or the eSPI controller transaction path.
The FF-A EC fan model is a service-level model, **not eSPI bus emulation**.
The evidence and explicit `service_not_booted` /
`transaction_contract_uncharacterised` blockers are in
[research/espi.json](../research/espi.json).
Wire transactions, completion/error forms and physical eSPI behaviour
remain **unverified**. Booting the extracted ESPI service and capturing
its transactions would be required before implementing that contract.

### Secure Boot provenance is a separate boundary

The PK and KEK are our disposable lab keys; the enrolled module MOK is
also a lab identity. `db` contains **only Microsoft Corporation UEFI CA
2011**, not the Canonical CA or that MOK. Canonical trust comes from the
unchanged shim's embedded vendor certificate. `dbx` is absent. The
synthetic loader and functional ROT **do not authenticate the vendor
firmware**. These are genuine UEFI/shim/kernel checks after entry into
unverified-by-the-model firmware, not a manufacturer's provisioned boot
ROM/BL31 root of trust. Certificate hashes, guest readback and the
`DBX_PRESENT=False` capture are linked from the
[Secure Boot provenance boundary](linux-under-the-profile.md#secure-boot-provenance-boundary).

## Runtime variables and a selected non-fallback boot entry

The earlier recovery-initramfs `Operation not supported` and installer
`efibootmgr` failure are pre-context-fix history. Fresh retained creation
run **`20261112T060832.086295Z`** explicitly unmounts/remounts efivarfs
and records `EFIVARFS_MOUNT_RC=0`,
`efivarfs efivarfs /sys/firmware/efi/efivars`, and
`EFIBOOTMGR_CREATE_RC=0`. The created entry is
`Boot0006* Spark runtime persistence`, loader
`\EFI\ubuntu\shimaa64.efi`.

After that harness/QEMU exited, a **new process** cold-boots retained
run **`20261112T063956.565964Z`**. Its UART reaches
`localhost login: root`; `efibootmgr -v` reports `BootCurrent: 0006`
and the same named entry and non-fallback loader. Creation-after and
cold-before NOR SHA-256 are identical. This is persisted and selected
NVRAM, not just a successful write before a warm reset. The
`EFI/BOOT` fallback remains an available path, not the only bootable one.

The existing real-STMM relay plus permission-context fix already supply
the service; no second variable database or forged EFI response was added.
The original isolated-disk root hash was restored after the authenticated
sessions, and both accepted Secure Boot stores were left unchanged.
See [the complete runtime evidence, request frame and custody](runtime-variables.md#fresh-runtime-create-and-cold-boot-persistence).
