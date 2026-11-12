# Verifying the fan service on a unit

`tools/fan_verify.sh` answers one question: **does the fan service this project models exist on this
machine, and does it answer the same way?**

It exists because the evidence is currently uneven. The constants the model uses — function id
`0xc400008d`, endpoint `0x8003`, status ids `0x06000704`/`0x06000706`, write commands `0x06000790..793` —
come from the **newest** firmware we hold (`5.36_0ACUM028`, vendor UEFI 2.158.15), and they were measured
working on **older** units (`5.36_0ACUM027`) whose ACPI does not publish a fan device at all. New-to-old is
measured; a unit on `ACUM028`, and anything later, is inference.

## What it does

Read-only by default:

1. records firmware, kernel, GPU, the hottest thermal zone and how many ACPI fan zones exist;
2. builds `kernel/spark_fan.c` against the running kernel's headers and loads it **without** `allow_pin`,
   so no write is possible;
3. reads both status ids, the cooling device's level, `pwm1` and `pwm1_enable`;
4. selects manual mode without `allow_pin` (module state only), then checks that `pwm1` refuses the write (`write_refused_without_allow_pin yes`); a missing hwmon device reports `n/a`, while failure to select manual mode is an error;
5. unloads and reports.

With `--write` it additionally pins the fans — first at the `_FSL` maximum, then at a 12500 rpm floor —
reading back after each step. **That leaves the fans pinned until the machine reboots**: nothing in the
interface releases the EC's clamps, and unloading the module or writing `pwm1_enable=2` does not.

It prints `key=value` lines so a report is a copy-paste.

## The baseline it was written against (one ACUM027 unit, 2026-11-11)

```
bios                   5.36_0ACUM027
acpi_fan_zones         0
fan1_rpm               2700
fan2_rpm               4050
cooling_level          91
pwm1                   232          (the measured level, 91/100 scaled to 0..255)
pwm1_enable            2            (the supervisor is not driving)
write_refused_without_allow_pin yes
```

The historical guard result above was recorded in automatic mode and did not isolate
`allow_pin`; the current verifier selects manual mode before attempting that write.

With `--write` on the same unit: a 13500 rpm floor reported **8820 / 12420 rpm** and was audibly louder
than the units beside it. A separate third-party project on the same hardware reports 2700/4050 idle and
9000/13500 at maximum, so those numbers agree across implementations.

## How to read a result on another firmware

| observation | meaning |
| --- | --- |
| `hwmon_present yes`, sane `fan*_rpm` | the service is there and answers; the model's constants hold |
| `fan*_rpm 0` with `cooling_level 0` | the service answered zero — check the fans are actually stopped before calling this a failure |
| `fan*_rpm` right but `cooling_level 0` | the level mapping differs on this firmware; report both numbers |
| `insmod` refused under Secure Boot | expected: the module is unsigned, so it needs a MOK enrolment or Secure Boot off |
| `write_refused_without_allow_pin no` | **stop** — the guard is not working on this build |

A run that reports different numbers is not a failure of the machine; it is the measurement that says the
model needs a per-firmware constant or, better, discovery from ACPI instead of compiled-in values.

## What this does not establish

- Nothing about a machine that is not run: a single unit's output is a single data point.
- Nothing about correctness of the fan *control* policy: the script pins and reads back, it does not
  evaluate a curve.
- Nothing on firmware that is not represented here at all: `ACUM028` units have never been driven.
