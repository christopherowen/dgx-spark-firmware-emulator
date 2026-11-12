# The EFI runtime variable service

The monitor relays runtime variable requests to the real Standalone MM (STMM)
firmware; it does not implement a second EFI variable database. The earlier
`Operation not supported` and installer `efibootmgr` failures are historical,
before the permission-context correction. Runtime writes already succeeded in
the [Secure Boot enrollment run](linux-under-the-profile.md#provisioning-path-and-custody)
`20261112T015108.967843Z`, and the following cold boot
`20261112T022953.618760Z` reports `BootCurrent: 0000` for a guest-created
`Spark shim fallback` boot entry. Its loader happens to be
`\EFI\BOOT\BOOTAA64.EFI`; that is not evidence of an unwritable NVRAM store.

## The chain, and which links are measured

```
Linux efivars / efibootmgr
  → firmware runtime GetVariable / SetVariable in EFI runtime memory
    → MM communication buffer at 0x933e0000 (128 KiB), FF-A direct request to endpoint 0x8004
      → STMM, running as a secure image this monitor boots ("MON: boot STMM" in every run)
        → the flash service at endpoint 0x8002, which this monitor serves
          → the persistent NOR model at flash offset 0x03000000
```

| link | state |
| --- | --- |
| firmware runtime → `0x8004` | **measured**: `monitor.c` saves the non-secure context and enters the secure one with the frame (`DIRECT_REQ`, NS, endpoint `0x8004`) |
| STMM running | **measured**: it reports `MON: STMM waiting` and answers `DIRECT_RESP` at level 2 |
| STMM → flash service `0x8002` | **measured**: the monitor serves reads, program and erase through the same model the flash lane uses, including its latch policy |
| the variable store itself | **modelled**: seeded NOR with a live overlay; these installed-guest runs use a legacy 1 MiB store, not a complete board flash part |
| ROT services `0x12`, `0x29` | Default/error transport refuses them; opt-in `--rot-functional` answers status 0 with no payload, allowing the observed firmware path to proceed |

The transport, real variable implementation and persistent NOR store therefore
already exist. The measured runtime blocker was unbanked permission-indirection
state leaking from Linux into STMM, not an absent `0x8004` service. The monitor
now saves/restores TCR2, PIR/PIRE0 and HCRX per world; see the
[fault and corrected-context evidence](linux-under-the-profile.md#the-installed-disk-boots-to-the-installed-kernel-under-the-vendor-uefi).
This does not turn the functional ROT replies into cryptographic services.

## Why the refusal is not simply "return success"

Two facts from earlier work close that shortcut:

- Answering the `0x8004` pair with flash semantics — the obvious one-line fix — makes the firmware take
  a synchronous exception immediately (`Synchronous Exception at 0x0000000084…`). Endpoint `0x8004` is a
  different service with a different protocol, not the flash service renumbered.
- The `spark-rot-error-replies` transport exists to prove a *failure* scenario. Its own test asserts it
  never returns service success (`tests/test_qemu_profile.py`,
  `test_rot_failure_transport_never_returns_service_success`), and the comment in the model says it
  "must never be presented as a successful security service". Answering `0` there would be a lie about
  authentication, not an implementation.

## What a runtime-variable test must demonstrate

Use the full vendor-UEFI boot runner, with no `efi=noruntime` argument, a
persistent `--state-file`, `--rot-functional`, and the installed NVMe image.
The monitor's `--flash-trace` records the direct-request register frame and
the returned MM-buffer header without replacing STMM's response.

The acceptance sequence is an actual efivarfs mount, a guest
`efibootmgr --create` with exit status 0, and a **separate cold boot** using
the saved firmware-written NOR. The second guest must list the same entry
and loader, select it as `BootCurrent`, and reach `localhost login:`.
A warm reset, host-side NOR editing, or a create command echoed without its
result is not persistence evidence. NVMe is snapshot-per-run; firmware
variables persist through the NOR state file, not through disk writes.

## Security boundary

Neither the synthetic loader nor functional ROT replies authenticate the
vendor UEFI/STMM images. Those unchanged images execute their own variable
and image-verification code, but there is no authenticated silicon-to-firmware
chain in this model. See the
[Secure Boot provenance boundary](linux-under-the-profile.md#secure-boot-provenance-boundary).


## What the first functional answer changed, measured

With `--rot-functional`, the two services the capture shows the firmware calling are answered with
status 0 and no payload, everything else still refused:

```
spark ROT: service 0x29 refused; request[2] = 0x00000129 0x90000000
spark ROT: service 0x29 answered functionally
spark ROT: service 0x12 refused; request[4] = 0x00000312 0x90000000 0x00000001 0xfcc40d97
spark ROT: service 0x12 answered functionally
spark ROT: service 0x12 refused; request[4] = 0x00000312 0x90000000 0x00000001 0xb6db6764
```

Three observations, none of them inference:

- **The fourth word changes between calls** — `0xfcc40d97` then `0xb6db6764`.
  A digest/checksum interpretation is **[INFERENCE]**, not an identified
  cryptographic algorithm or an established service semantic.
- **The MM header/payload length changes across the three attempts** (`0x28`, `0x8d`, `0x44`).
  This is `MessageLength` at buffer +16, after the GUID. The 32-byte capture does not reach the
  variable-service `ReturnStatus`, so no status interpretation is established.
- **The firmware passes the checkpoint it used to hang at** (`A9`, `AB`) and reaches the setup UI.

That first capture established only checkpoint progress and setup: the harness
also typed ESC at `Press <DEL>`, which can enter AMI Setup. The later
`--no-setup-escape` installed-disk boots independently establish Linux startup,
runtime writes and UEFI image-signature enforcement. They still do not establish
ROT authentication or its complete reply contract.

## The request shape, as far as the captures support it

The raw request words are measured; interpretation of their fields is not
an identified protocol specification. Retained host diagnostics in
`20261111T093421.135412Z/qemu.log` lines 40–41 and 106–119 show:

| service | request words | observed variations |
| --- | --- | --- |
| `0x29` | `0x00000129 0x90000000` | repeated later in the same capture; not necessarily once per boot |
| `0x12` | `0x00000312 0x90000000 0x00000001 0xfcc40d97` | third word `1` or `2`; fourth word `0xfcc40d97`, `0xb6db6764` or `0x64a0ea5f` |

Those are QEMU transport diagnostics, not guest UART output; no claim is
made that they appeared in `serial.log`. The earlier two-call example
above is a subset, not an exhaustive per-boot count.

The leading words encode the observed service id in the low byte, with `1`
or `3` in the next byte matching the number of following argument words.
That count interpretation is **[INFERENCE]**. `0x29` has one observed
argument, `0x90000000`, in the model's secure memory. `0x12` has the same
address, a third word of `1` or `2`, and a varying fourth word. Whether
that last word is a checksum, digest fragment, token or another field
is **unverified**.

The **reply shape is unknown beyond "status 0 is accepted"** on this observed
path. The functional transport supplies no payload words. Checkpoint progress
and a working variable service do not prove that an empty success reply is
correct on real hardware or for another request.

To settle the contract, retain paired request/reply captures from a real ROT
for these exact words, including mailbox length/status, every response word,
and any memory modified at `0x90000000`, then trace the vendor caller's
post-reply loads/branches to distinguish ignored fields from validated ones.
An identified vendor implementation or authoritative protocol specification
could supply the same missing information. Only then can executable
request/reply and negative-case probes validate a functional implementation;
returning status 0 alone is not that implementation.

## Fresh runtime create and cold-boot persistence

The creation run is **`20261112T060832.086295Z`**, using unchanged vendor
UEFI/STMM **2.155.11**, the installed NVMe image, `--memory 2G`,
`--rot-functional --no-setup-escape --ffa-ec --flash-trace`, and a separate
copy of the Secure-Boot-on NOR. The accepted on/off stores were not
modified. Its authenticated serial session records these actual outputs
in `serial.log` (ANSI stripped); the session includes a malformed initial attempt before the successful create shown below:

```text
RUNTIME_ROOT_UID=0
EFIVARFS_MOUNT_RC=0
efivarfs efivarfs /sys/firmware/efi/efivars
EFIBOOTMGR_CREATE_RC=0
Boot0006* Spark runtime persistence	HD(1,GPT,6d265c09-2096-4d95-818a-f43e623bf1fd,0x800,0x95000)/File(\EFI\ubuntu\shimaa64.efi)
```

The mount result follows an explicit unmount and
`mount -t efivarfs efivarfs /sys/firmware/efi/efivars`, not just the
presence of an empty directory. The create result follows:

```sh
efibootmgr --create --disk /dev/nvme0n1 --part 1 \
    --label 'Spark runtime persistence' --loader '\EFI\ubuntu\shimaa64.efi'
```

During that command, the trace records the actual runtime request:

```text
RELAY-IN ep=0x0000000000008004 elr3=0x0000000087991f08 spsr3=0x0000000061402009 w=0x00000000c400006f,0x0000000000008004,0x0000000000000000,0x00000000c4000041,0x0000000000000000,0x00000000933e0000,0x0000000000000000,0x0000000000000000,
FLASH cmd=01 req=000000b4 addr=0000000003003408 count=ffffffff status=00
```

The frame is FF-A `DIRECT_REQ`, NS endpoint 0 to STMM `0x8004`,
MM-communication selector `0xc4000041`, pointer `0x933e0000`.
The real STMM response is relayed back as `DIRECT_RESP` with
`x3=0xc4000061`; the separate flash command is handled through the
existing trusted `0x8004` → `0x8002` flash path. No guest variable
payload or successful EFI reply is invented by the monitor.

The harness was stopped normally, saving firmware-written NOR with
SHA-256 `6ade9633815de759c713584f82ea943f8cf130a05442291c748be60965afd861`.
The original guest root hash was preserved before temporarily replacing
only that isolated disk's root-password hash for authenticated serial
access; the transient password was not retained in the captures.

### Separate-process cold boot

The first harness and QEMU exited with status 0. A new `boot_spark.py`
process then cold-booted **`20261112T063956.565964Z`** from the same saved
NOR, with the same arguments except that `--flash-trace` was omitted.
Its `run.json` records the **same**
`6ade9633815de759c713584f82ea943f8cf130a05442291c748be60965afd861`
as `state_sha256_before`, equal to the creation run's
`state_sha256_after`. This was not `reboot` inside one QEMU instance.

That second `serial.log` records (ANSI stripped):

```text
localhost login: root
COLD_ROOT_UID=0
BootCurrent: 0006
BootOrder: 0006,0002,0000
Boot0006* Spark runtime persistence	HD(1,GPT,6d265c09-2096-4d95-818a-f43e623bf1fd,0x800,0x95000)/File(\EFI\ubuntu\shimaa64.efi)
COLD_EFIBOOTMGR_RC=0
efivarfs efivarfs /sys/firmware/efi/efivars
```

The entry **survived a cold boot and was selected as BootCurrent**, with
the non-fallback loader `\EFI\ubuntu\shimaa64.efi`. The earlier
`Boot0000` entry still names `\EFI\BOOT\BOOTAA64.EFI`; it is not the
selected entry in this run. Removable-media fallback remains available
when firmware does not choose a usable NVRAM option, but it is no longer
the only demonstrated boot path.

The cold harness also exited with status 0. The isolated disk's original
root hash was restored byte-for-byte after both guests stopped. No
password, private key, source-disk modification or raw execution capture
is committed. Each ignored run retains `runtime-variable-evidence.json`
with original-log hashes, ANSI-stripped line numbers, exact positive
outputs and the matching NOR custody hashes. The source disk is otherwise
read-only to these snapshot-per-run guests; the independent proof NOR
retains the new entry.

No additional variable-service implementation was necessary: the existing
relay, real STMM, flash model and permission-context fix satisfy this
path. The only monitor-source change for this evidence is correcting a
stale comment that called the endpoint unserved. Installer reinstallation,
all variable attributes/authentication cases, crash/power-loss recovery
and FF-A conformance are **not revalidated** by this experiment. The ROT,
optional-context, SMMU, eSPI and firmware-authentication limits remain as
[documented separately](boot-status.md#remaining-platform-limits-and-evidence-boundaries).
