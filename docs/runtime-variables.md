# The EFI runtime variable service

## Historical refusal-only investigation

The chain and plan below describe the earlier refusing transport, before the functional
replies documented in the next section; they are not the current implementation status.

Why `efibootmgr` and `efivars` fail on this profile, what already works, and the order in which the
remainder can be implemented without guessing.

### The chain, and which links are measured

```
Linux efivars / efibootmgr
  → firmware runtime GetVariable / SetVariable in EFI runtime memory
    → MM communication buffer at 0x933e0000 (128 KiB), FF-A direct request to endpoint 0x8004
      → STMM, running as a secure image this monitor boots ("MON: boot STMM" in every run)
        → the flash service at endpoint 0x8002, which this monitor serves
          → the variable partition of the NOR model (32 MiB at flash 0x03000000)
```

| link | state |
| --- | --- |
| firmware runtime → `0x8004` | **measured**: `monitor.c` saves the non-secure context and enters the secure one with the frame (`DIRECT_REQ`, NS, endpoint `0x8004`) |
| STMM running | **measured**: it reports `MON: STMM waiting` and answers `DIRECT_RESP` at level 2 |
| STMM → flash service `0x8002` | **measured**: the monitor serves reads, program and erase through the same model the flash lane uses, with the latch policy at `0x1e3a4` |
| the variable store itself | **modelled**: 32 MiB variable partition, seeded, with a live overlay |
| ROT services `0x12`, `0x29` | **refused**: the opt-in `spark-rot-error-replies` transport answers status `1` (unavailable) to every request |

So the transport and the store exist. The observed dependency is on two ROT services whose complete semantics are unverified:
STMM asks for them and the refusing transport returns an error.

### Why the refusal is not simply "return success"

Two facts from earlier work close that shortcut:

- Answering the `0x8004` pair with flash semantics — the obvious one-line fix — makes the firmware take
  a synchronous exception immediately (`Synchronous Exception at 0x0000000084…`). Endpoint `0x8004` is a
  different service with a different protocol, not the flash service renumbered.
- The `spark-rot-error-replies` transport exists to prove a *failure* scenario. Its own test asserts it
  never returns service success (`tests/test_qemu_profile.py`,
  `test_rot_failure_transport_never_returns_service_success`), and the comment in the model says it
  "must never be presented as a successful security service". Answering `0` there would be a lie about
  authentication, not an implementation.

### What is implemented now

The transport records the **whole** refused request, not just its service id:

```
spark ROT: service 0x29 refused; request[2] = 0x00000129 0x…
```

That is the only sound starting point: a service implemented from its own refused requests is
reconstructed from evidence, while a service implemented from a guess about what a ROT "probably" does
is how the firmware was crashed before.

### Order of work

1. **Capture both services.** Run the firmware boot with the model's log enabled and record every
   `service 0x12` and `service 0x29` request with its full payload: which words are constants, which
   are addresses, which change between boots, and what the caller does with the reply it gets back.
   Two runs are the minimum, because one run cannot distinguish a constant from a value that happened
   to be constant.
2. **Name the request shape.** From the payload and the caller's subsequent behaviour, write down the
   request and reply layout before implementing anything. If the shape cannot be named from two
   captures, capture more — do not interpolate.
3. **Implement only what the capture shows**, one service at a time, each with a probe that exercises
   the exchange through the transport rather than asserting on source text. `0x29` is the smaller
   request (one payload word observed) and is the first one to attempt.
4. **Then remove `efi=noruntime` from the profile command line** and assert the milestone that has never
   held: the guest mounts `efivars` and `efibootmgr` lists and creates an entry. Until that asserts, the
   profile keeps the flag and the limitation is stated rather than papered over — which is what
   `docs/linux-under-the-profile.md` already records.
5. **Re-run the installer** afterwards: its last failing step is `efibootmgr --create`, so a working
   variable service should turn `Error: Failed to find the newly created Ubuntu entry` into a success,
   and that is a far better proof than any unit test.

### What this will not do

It will not make the profile authenticate anything. A ROT service that returns a well-formed answer to
a firmware caller is a functional model, not a security boundary, and nothing here should be described
as secure boot or measured boot. The value is that the firmware's own code paths stop failing for want
of an answer, which is what a fidelity emulator is for.

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

What that does *not* establish is that a boot completes: the setup UI appeared in a run where this
harness also typed ESC at the firmware's `Press <DEL>` prompt, and ESC is how AMI enters Setup. The
harness therefore grew `--no-setup-escape`, so our own keypress cannot be mistaken for the firmware's
behaviour, and the disk-boot question is being re-asked with it suppressed. A functional ROT answer is
still not an authentication boundary and nothing here claims one.


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

