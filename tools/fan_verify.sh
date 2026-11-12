#!/bin/sh
# fan_verify.sh — check, on a DGX Spark, whether the fan service this project models is present.
#
# Read-only by default. It builds the module, loads it WITHOUT allow_pin, reads both status ids twice
# each (the service answers the first read after a gap with zero), reads the thermal cooling device, and
# unloads. No fan level is ever written unless you pass --write.
#
#   --write    also pin the fans, first at the _FSL maximum and then at a 12500 rpm floor, reading back
#              after each step. This PINS THE FANS UNTIL REBOOT: nothing in the interface releases the
#              EC's clamps, and unloading the module or writing pwm1_enable=2 does not. Run it only if
#              you can reboot the machine afterwards, and only on a machine where louder fans are
#              acceptable.
#
# Nothing here needs network access, and nothing is written outside a temporary build directory.
#
# Usage:
#   ./fan_verify.sh                 # read-only
#   ./fan_verify.sh --write         # read, then pin and report (reboot to undo)
#   MODULE=/path/to/spark_fan.ko ./fan_verify.sh
#
# It prints `key=value` lines: paste the whole block into a report. The values to compare against the
# 2026-11-11 ACUM027 baseline are documented in docs/fan-verify-on-a-unit.md.

set -u

WRITE=0
[ "${1:-}" = "--write" ] && WRITE=1
[ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ] && { sed -n '2,25p' "$0"; exit 0; }

say() { printf '%s\n' "$*"; }
kv()  { printf '%-22s %s\n' "$1" "$2"; }

hottest() {
	m=0
	for z in /sys/class/thermal/thermal_zone*; do
		t=$(( $(cat "$z/temp" 2>/dev/null || echo 0) / 1000 ))
		[ "$t" -gt "$m" ] && m=$t
	done
	echo "$m"
}
hwmon_dir() {
	h=$(grep -l '^spark_fan$' /sys/class/hwmon/hwmon*/name 2>/dev/null | head -1)
	[ -n "$h" ] && dirname "$h"
}
cooling_dir() {
	c=$(grep -l '^spark-fan$' /sys/class/thermal/cooling_device*/type 2>/dev/null | head -1)
	[ -n "$c" ] && dirname "$c"
}

say "=== fan_verify.sh $(date -u +%Y-%m-%dT%H:%M:%SZ)"
kv "host" "$(hostname)"
kv "kernel" "$(uname -r)"
kv "bios" "$(sudo -n dmidecode -s bios-version 2>/dev/null || echo 'needs sudo -n')"
fan_zones() { n=0; for f in /sys/class/thermal/thermal_zone*/type; do [ -r "$f" ] || continue; case $(cat "$f") in FAN0|FAN1) n=$((n + 1));; esac; done; echo "$n"; }
kv "acpi_fan_zones" "$(fan_zones)"
kv "hottest_zone_C" "$(hottest)"
kv "gpu" "$(nvidia-smi --query-gpu=name,temperature.gpu,power.draw --format=csv,noheader 2>/dev/null | head -1)"

# 1. Build if no module was supplied.
if [ -z "${MODULE:-}" ]; then
	here=$(cd "$(dirname "$0")" && pwd)
	src="$here/../kernel"
	[ -f "$src/spark_fan.c" ] || { say "ERROR: pass MODULE=/path/to/spark_fan.ko, or run this from tools/ in the repository"; exit 1; }
	build=$(mktemp -d)
	cp "$src/spark_fan.c" "$src/Makefile" "$build"/
	kv "build_dir" "$build"
	if ! make -C "/lib/modules/$(uname -r)/build" M="$build" modules > "$build/build.log" 2>&1; then
		say "ERROR: module build failed; last lines:"
		tail -5 "$build/build.log"
		exit 1
	fi
	MODULE="$build/spark_fan.ko"
fi
kv "module" "$MODULE"

# 2. Read-only pass: no allow_pin, so no write is possible.
sudo -n rmmod spark_fan 2>/dev/null
if ! sudo -n insmod "$MODULE"; then
	say "ERROR: insmod failed; if this is Secure Boot, the module needs a MOK enrolment or Secure Boot off"
	exit 1
fi
sleep 1
d=$(hwmon_dir); c=$(cooling_dir)
kv "hwmon_present" "$([ -n "$d" ] && echo yes || echo no)"
kv "cooling_present" "$([ -n "$c" ] && echo yes || echo no)"
kv "fan1_rpm" "$([ -n "$d" ] && cat "$d/fan1_input" 2>/dev/null || echo n/a)"
kv "fan2_rpm" "$([ -n "$d" ] && cat "$d/fan2_input" 2>/dev/null || echo n/a)"
kv "cooling_level" "$([ -n "$c" ] && cat "$c/cur_state" 2>/dev/null || echo n/a)"
kv "pwm1" "$([ -n "$d" ] && cat "$d/pwm1" 2>/dev/null || echo n/a)"
kv "pwm1_enable" "$([ -n "$d" ] && cat "$d/pwm1_enable" 2>/dev/null || echo n/a)"
if [ -z "$d" ]; then
	kv "write_refused_without_allow_pin" "n/a"
elif ! echo 1 | sudo -n tee "$d/pwm1_enable" >/dev/null; then
	say "ERROR: cannot select manual mode for the allow_pin check"
	sudo -n rmmod spark_fan 2>/dev/null
	exit 1
else
	# Selecting manual mode changes only module state; without allow_pin no EC write is possible.
	kv "write_refused_without_allow_pin" \
	   "$(echo 200 | sudo -n tee "$d/pwm1" >/dev/null 2>&1 && echo NO || echo yes)"
fi

# 3. Optional write pass. This is the part that leaves the fans pinned.
if [ "$WRITE" -eq 1 ]; then
	say ""
	say "--- write pass: the fans will stay pinned until this machine reboots"
	sudo -n rmmod spark_fan 2>/dev/null
	if ! sudo -n insmod "$MODULE" allow_pin=1; then
		say "ERROR: insmod allow_pin=1 failed"; exit 1
	fi
	sleep 1
	d=$(hwmon_dir); c=$(cooling_dir)
	if [ -z "$d" ]; then say "ERROR: no hwmon device after allow_pin load"; exit 1; fi
	# a) the _FSL maximum, which is a 4350 rpm floor, not the fan's limit
	echo 1   | sudo -n tee "$d/pwm1_enable" >/dev/null
	echo 255 | sudo -n tee "$d/pwm1" >/dev/null
	sleep 6
	kv "level_max_fan1_rpm" "$(cat "$d/fan1_input")"
	kv "level_max_fan2_rpm" "$(cat "$d/fan2_input")"
	kv "level_max_cooling" "$([ -n "$c" ] && cat "$c/cur_state" || echo n/a)"
	# b) a floor above the _FSL scale, written the way the transport carries it
	sudo -n rmmod spark_fan 2>/dev/null
	if sudo -n insmod "$MODULE" allow_pin=1 rpm_floor=12500; then
		sleep 8
		d=$(hwmon_dir)
		kv "floor12500_fan1_rpm" "$(cat "$d/fan1_input")"
		kv "floor12500_fan2_rpm" "$(cat "$d/fan2_input")"
		kv "floor12500_audible" "operator's judgement: louder than idle? (record yes/no)"
	else
		kv "floor12500" "insmod with rpm_floor failed"
	fi
	kv "restore" "REBOOT required: nothing in this interface releases the EC clamps"
fi

sudo -n rmmod spark_fan 2>/dev/null
kv "module_unloaded" "$([ -z "$(lsmod | grep spark_fan)" ] && echo yes || echo no)"
say "=== end"
