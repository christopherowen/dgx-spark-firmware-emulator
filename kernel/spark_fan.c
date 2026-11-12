// SPDX-License-Identifier: GPL-2.0
/*
 * spark_fan - drive the DGX Spark's fans through the EL3 service their ACPI device describes.
 *
 * What it does: issues the same SMCCC call the firmware's own AML does (0xC400008D, endpoint 0x8003,
 * the OEM service GUID, the request word \_SB.FAN0.WRB builds), and exposes it as a cooling device.
 *
 * It exists to be *testable* and *conservative*, not because ACPI is broken: measured on sp8
 * (2026-11-09, read-only), the vendor kernel has CONFIG_ACPI_FFH=y and \_SB.FAN0._FST returns live
 * per-fan RPM (1978 and 2072). The "Aborting method WRB" this repository once recorded was the
 * recovery kernel inside QEMU, not the firmware. So on a unit, /sys/class/thermal already works and
 * this module is only interesting when the ACPI route is unavailable.
 *
 * THE IMPORTANT PART: on the unit, 0x06000790 and 0x06000792 are the *upper and lower clamps* in EC
 * memory (host 0x06000600 = EC 0x119000). Writing the same value into both - which is what \_FSL and
 * this module do - pins the fans at one speed, stops NVIDIA's curve from ramping, caps them at the
 * level's rpm (at most 4350, against fan 0's 9000) and **nothing in ACPI puts the clamps back to
 * 0xFFFF (automatic)**. A reboot is the only measured way back. So pinning is off by default and
 * needs an explicit module parameter.
 *
 *   write: x4 = 0x09 | (command << 8) | (value << 40), command 0x06000790..793
 *   read:  x4 = 0x0C | (0x06000704 << 8) | (2 << 40)          (0x06000706 for fan 1)
 *
 * The level arithmetic is the DSDT's, not this module's invention:
 *
 *   rpm = FMIN + ((FMAX - FMIN) * level) / 100           FMIN 975, FMAX 4350, level 0..100
 *
 * and \_SB.FAN0._FIF reports fine-grained control with step 1, which is why a level is a level and
 * not an index into a state table. Nothing here pins an EC capability revision: if the service
 * stops answering, the write reports -EIO and the cooling device says so.
 */

#ifndef SPARK_FAN_HOST_TEST
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/thermal.h>
#include <linux/hwmon.h>
#include <linux/platform_device.h>
#include <linux/arm-smccc.h>
#include <linux/errno.h>
#endif

#define SPARK_FAN_FUNCTION   0xC400008DUL
#define SPARK_FAN_ENDPOINT   0x8003UL
#define SPARK_FAN_GUID_HI    0x20418532A0634A88ULL
#define SPARK_FAN_GUID_LO    0x46A5A008C0EEAA83ULL
#define SPARK_FAN_COMPLETION 0xC400008EUL
#define SPARK_FAN_OP_WRITE   0x09U
#define SPARK_FAN_OP_READ    0x0CU
#define SPARK_FAN_COMMAND_BASE   0x06000790U
#define SPARK_FAN_COMMAND_STATUS0 0x06000704U
#define SPARK_FAN_COMMAND_STATUS1 0x06000706U
#define SPARK_FAN_RPM_MIN 975U
#define SPARK_FAN_RPM_MAX 4350U
#define SPARK_FAN_LEVEL_MAX 100UL



#ifndef SPARK_FAN_HOST_TEST
#include <linux/moduleparam.h>
typedef u64 spark_u64;
#define SPARK_FAN_KEEP
#else
#include <stdbool.h>
typedef unsigned long long spark_u64;
#define SPARK_FAN_KEEP __attribute__((unused))
#endif

/* Pinning state. Declared here so the guard below has something to read: writing a level pins the
 * EC's clamps and no ACPI path restores them, so it stays off unless the module is loaded with
 * spark_fan.allow_pin=1. */
static SPARK_FAN_KEEP bool allow_pin;
/* A floor in rpm, written once at load.
 *
 * `_FSL`'s own scale is a *floor* that tops out at 4350 rpm (975 + 3375·level/100), which is why level
 * 100 is not audible: the EC is free to run the fans wherever it likes above it. The transport carries
 * the rpm as two little-endian bytes, so a higher floor can be asked for directly. 0 disables it. */
static SPARK_FAN_KEEP unsigned int rpm_floor;
static SPARK_FAN_KEEP bool warned;

/** The request word \_SB.FAN0.WRB builds for a write. */
static SPARK_FAN_KEEP spark_u64 spark_fan_write_request(unsigned command, unsigned value)
{
	return (spark_u64)(SPARK_FAN_OP_WRITE | ((spark_u64)command << 8) |
			   ((spark_u64)(value & 0xffff) << 40));
}

/** The request word \_SB.FAN0._FST builds for the two status reads. */
static SPARK_FAN_KEEP spark_u64 spark_fan_read_request(unsigned command)
{
	return (spark_u64)(SPARK_FAN_OP_READ | ((spark_u64)command << 8) | ((spark_u64)2 << 40));
}

/** The level a measured rpm came from, rounding to the nearest step rather than flooring.
 *
 * \_SB.FAN0.WFCF maps level to rpm by integer division, so 50 -> 2662 and flooring 2662 back gives
 * 49: a state that reads one lower than the one just set. Rounding halves the error instead of
 * always biasing it down. The firmware's own \_SB.FAN0._FST carries the level as its Control field,
 * which is the authoritative figure when a caller needs it exactly.
 */
static SPARK_FAN_KEEP unsigned long spark_fan_level_for_rpm(unsigned rpm)
{
	if (rpm <= SPARK_FAN_RPM_MIN)
		return 0;
	if (rpm >= SPARK_FAN_RPM_MAX)
		return SPARK_FAN_LEVEL_MAX;
	return ((unsigned long)(rpm - SPARK_FAN_RPM_MIN) * 100 * 2 + (SPARK_FAN_RPM_MAX - SPARK_FAN_RPM_MIN)) /
	       (2 * (SPARK_FAN_RPM_MAX - SPARK_FAN_RPM_MIN));
}

/** The rpm the DSDT's WFCF computes for a level 0..100. */
static SPARK_FAN_KEEP unsigned spark_fan_rpm_for_level(unsigned long level)
{
	if (level > SPARK_FAN_LEVEL_MAX)
		level = SPARK_FAN_LEVEL_MAX;
	return SPARK_FAN_RPM_MIN + ((SPARK_FAN_RPM_MAX - SPARK_FAN_RPM_MIN) * (unsigned)level) / 100;
}

/* The rule the read path applies, outside the kernel-facing half so a host test can execute it:
 * the highest level any answering id reports, and whether anything answered at all. */
static SPARK_FAN_KEEP unsigned long spark_fan_best_level(const unsigned long *levels, unsigned count)
{
	unsigned long best = 0;
	for (unsigned i = 0; i < count; i++)
		if (levels[i] > best)
			best = levels[i];
	return best;
}

#ifndef SPARK_FAN_HOST_TEST

module_param(allow_pin, bool, 0444);
module_param(rpm_floor, uint, 0444);
MODULE_PARM_DESC(rpm_floor,
		 "write this rpm as the fan floor once at load (0 = do not); the _FSL scale tops out at 4350 rpm");
MODULE_PARM_DESC(allow_pin,
		 "permit writing a level (pins the EC clamps; a reboot is the only way back)");

/*
 * The service answers in x4, and Linux's arm_smccc_res carries only a0..a3: the value the firmware
 * returns would be dropped by arm_smccc_smc(). So the call is made here, with the registers named
 * explicitly, exactly as the EFI probe does it.
 *
 * Returns 0 when the completion id came back in x0, and the answer word in *value.
 */
static int spark_fan_call(u64 request, u64 *value)
{
	register u64 x0 __asm__("x0") = SPARK_FAN_FUNCTION;
	register u64 x1 __asm__("x1") = SPARK_FAN_ENDPOINT;
	register u64 x2 __asm__("x2") = SPARK_FAN_GUID_HI;
	register u64 x3 __asm__("x3") = SPARK_FAN_GUID_LO;
	register u64 x4 __asm__("x4") = request;

	__asm__ volatile("smc #0"
			 : "+r"(x0), "+r"(x1), "+r"(x2), "+r"(x3), "+r"(x4)
			 :
			 : "x5", "x6", "x7", "x8", "x9", "x10", "x11", "x12", "x13", "x14",
			   "x15", "x16", "x17", "memory");
	if (x0 != SPARK_FAN_COMPLETION)
		return -EIO;
	if (value)
		*value = x4;
	return 0;
}

static int spark_fan_write_bytes(unsigned command, unsigned value)
{
	return spark_fan_call(spark_fan_write_request(command, value), NULL);
}

/* Read one status id, and discard the first answer.
 *
 * Measured on a unit (2026-11-11, one unit): the first read after a gap is answered **zero** with a
 * success completion code, and the second carries the rpm. That is why the cooling path never saw it —
 * its `0x06000704` read was always the first call of a pair and its `0x06000706` read always the
 * second — and why an earlier version of this module concluded that one id answers zero and the other
 * carries the fan. Both ids carry the same reading; the zero was the warm-up.
 */
static int spark_fan_read_rpm(u32 id, unsigned *rpm)
{
	u64 answer = 0;
	int rc;

	for (int attempt = 0; attempt < 2; attempt++) {
		answer = 0;
		rc = spark_fan_call(spark_fan_read_request(id), &answer);
		if (rc)
			return rc;
	}
	*rpm = (unsigned)answer & 0xffff;
	return 0;
}

static int spark_fan_set_level(unsigned long level)
{
	unsigned rpm = spark_fan_rpm_for_level(level);
	int ret;

	/* \_SB.FAN0.WFCF sends the same rpm to both fans, low byte then high byte. */
	ret = spark_fan_write_bytes(SPARK_FAN_COMMAND_BASE + 0, rpm & 0xff);
	if (ret)
		return ret;
	ret = spark_fan_write_bytes(SPARK_FAN_COMMAND_BASE + 1, (rpm >> 8) & 0xff);
	if (ret)
		return ret;
	ret = spark_fan_write_bytes(SPARK_FAN_COMMAND_BASE + 2, rpm & 0xff);
	if (ret)
		return ret;
	return spark_fan_write_bytes(SPARK_FAN_COMMAND_BASE + 3, (rpm >> 8) & 0xff);
}

static int spark_fan_get_max_state(struct thermal_cooling_device *cdev,
				   unsigned long *state)
{
	(void)cdev;
	*state = SPARK_FAN_LEVEL_MAX;
	return 0;
}

/* Both documented status ids are read, and the highest level wins.
 *
 * `0x06000704` is the id \_SB.FAN0._FST uses and `0x06000706` the id \_SB.FAN1._FST uses, so they are
 * two fans on the reference unit. Measured on an ACUM027 unit (2026-11-10, Secure Boot off, module
 * built with the unit's own kbuild): `0x06000704` answers 0 and `0x06000706` answers 4050 rpm, stable
 * across repeated reads with the chassis at 41.8 C. Reading only the first id therefore reported a
 * stopped fan on a machine whose fan was running; reading both and taking the larger level is the
 * conservative reading of an answer that is split across the pair.
 */
static int spark_fan_get_cur_state(struct thermal_cooling_device *cdev,
				   unsigned long *state)
{
	static const u32 ids[] = { SPARK_FAN_COMMAND_STATUS0, SPARK_FAN_COMMAND_STATUS1 };
	unsigned long levels[sizeof(ids) / sizeof(ids[0])];
	unsigned answered = 0;
	(void)cdev;

	for (unsigned i = 0; i < sizeof(ids) / sizeof(ids[0]); i++) {
		unsigned rpm = 0;

		if (spark_fan_read_rpm(ids[i], &rpm))
			continue;      /* one id refusing does not make the other id's answer wrong */
		levels[answered++] = spark_fan_level_for_rpm(rpm);
	}
	if (!answered)
		return -EIO;
	*state = spark_fan_best_level(levels, answered);
	return 0;
}

static int spark_fan_set_cur_state(struct thermal_cooling_device *cdev,
				   unsigned long state)
{
	(void)cdev;
	if (!allow_pin) {
		if (!warned) {
			warned = true;
			pr_warn("spark_fan: refusing to pin the fans; load with "
				"spark_fan.allow_pin=1 and expect a reboot to undo it\n");
		}
		return -EPERM;
	}
	return spark_fan_set_level(state);
}

static const struct thermal_cooling_device_ops spark_fan_ops = {
	.get_max_state = spark_fan_get_max_state,
	.get_cur_state = spark_fan_get_cur_state,
	.set_cur_state = spark_fan_set_cur_state,
};

static struct thermal_cooling_device *spark_fan_cdev;


/* ---- hwmon: what fancontrol and pwmconfig actually need --------------------------------------
 *
 * Three facts from the measured write path (2026-11-11, one unit) shape this interface:
 *
 *   - the EC honours a level write and the value sticks;
 *   - `cur_state` and RPM readback report the service's published estimate, not a physical
 *     tachometer measurement, and can lag a setpoint by seconds;
 *   - in manual mode `pwm1` reports what was asked for, while `fan*_input` reports that
 *     estimate. A disagreement does not by itself establish a physical fan ramp.
 *
 * `pwm1_enable` is the ownership flag: 2 is "the supervisor is not driving", 1 is manual. Writing 2
 * does **not** hand the EC's clamps back to the firmware — nothing in this interface can, because the
 * pin ends only at reset — so 2 means "I have stopped writing", not "firmware is in control again".
 * A reboot is the only restore, and the module's warning says so.
 */
#define SPARK_FAN_PWM_MAX 255
static unsigned long spark_fan_setpoint = SPARK_FAN_LEVEL_MAX;
static unsigned int spark_fan_pwm_enable = 2;

static umode_t spark_fan_is_visible(const void *data, enum hwmon_sensor_types type, u32 attr,
				    int channel)
{
	(void)data;
	(void)channel;
	switch (type) {
	case hwmon_fan:
		return attr == hwmon_fan_input ? 0444 : 0;
	case hwmon_pwm:
		if (attr == hwmon_pwm_input)
			return 0644;
		if (attr == hwmon_pwm_enable)
			return 0644;
		return 0;
	default:
		return 0;
	}
}

static int spark_fan_hwmon_read(struct device *dev, enum hwmon_sensor_types type, u32 attr,
				int channel, long *val)
{
	(void)dev;
	switch (type) {
	case hwmon_fan: {
		u32 id = channel == 0 ? SPARK_FAN_COMMAND_STATUS0 : SPARK_FAN_COMMAND_STATUS1;
		unsigned rpm = 0;

		if (spark_fan_read_rpm(id, &rpm))
			return -EIO;
		*val = (long)rpm;
		return 0;
	}
	case hwmon_pwm:
		if (attr == hwmon_pwm_input) {
			/* Manual: what was asked for. Otherwise: what the fans are actually at, scaled — a
			 * supervisor that reads 255 while the firmware is idling the fans at 91 would think
			 * something else is driving them to maximum. */
			if (spark_fan_pwm_enable == 1) {
				*val = (long)(spark_fan_setpoint * SPARK_FAN_PWM_MAX / SPARK_FAN_LEVEL_MAX);
			} else {
				unsigned long level = 0;

				if (spark_fan_get_cur_state(NULL, &level))
					return -EIO;
				*val = (long)(level * SPARK_FAN_PWM_MAX / SPARK_FAN_LEVEL_MAX);
			}
			return 0;
		}
		if (attr == hwmon_pwm_enable) {
			*val = (long)spark_fan_pwm_enable;
			return 0;
		}
		return -EOPNOTSUPP;
	default:
		return -EOPNOTSUPP;
	}
}

static int spark_fan_hwmon_write(struct device *dev, enum hwmon_sensor_types type, u32 attr,
				 int channel, long val)
{
	unsigned long level;
	int ret;
	(void)dev;
	(void)channel;

	if (type != hwmon_pwm)
		return -EOPNOTSUPP;
	if (attr == hwmon_pwm_enable) {
		if (val != 1 && val != 2)
			return -EINVAL;
		spark_fan_pwm_enable = (unsigned int)val;
		return 0;
	}
	if (attr != hwmon_pwm_input)
		return -EOPNOTSUPP;
	if (val < 0 || val > SPARK_FAN_PWM_MAX)
		return -EINVAL;
	if (spark_fan_pwm_enable != 1)
		return -EPERM;
	/* The same guard the cooling device has: pwm1 must not become a way around `allow_pin`, or the
	 * module's own promise — "writing a level pins the EC's clamps and no ACPI path restores them" —
	 * would hold only for one of its two interfaces. */
	if (!allow_pin) {
		if (!warned) {
			warned = true;
			pr_warn("spark_fan: refusing to pin the fans; load with "
				"spark_fan.allow_pin=1 and expect a reboot to undo it\n");
		}
		return -EPERM;
	}
	level = (unsigned long)val * SPARK_FAN_LEVEL_MAX / SPARK_FAN_PWM_MAX;
	ret = spark_fan_set_level(level);
	if (!ret)
		spark_fan_setpoint = level;
	return ret;
}

static const struct hwmon_ops spark_fan_hwmon_ops = {
	.is_visible = spark_fan_is_visible,
	.read = spark_fan_hwmon_read,
	.write = spark_fan_hwmon_write,
};

static const struct hwmon_channel_info *const spark_fan_info[] = {
	HWMON_CHANNEL_INFO(fan, HWMON_F_INPUT, HWMON_F_INPUT),
	HWMON_CHANNEL_INFO(pwm, HWMON_PWM_INPUT | HWMON_PWM_ENABLE),
	NULL
};

static const struct hwmon_chip_info spark_fan_chip = {
	.ops = &spark_fan_hwmon_ops,
	.info = spark_fan_info,
};

static struct device *spark_fan_hwmon;

static int __init spark_fan_init(void)
{
	spark_fan_cdev = thermal_cooling_device_register("spark-fan", NULL, &spark_fan_ops);
	if (IS_ERR(spark_fan_cdev))
		return PTR_ERR(spark_fan_cdev);
	spark_fan_hwmon = hwmon_device_register_with_info(&platform_bus, "spark_fan", NULL,
							  &spark_fan_chip, NULL);
	if (IS_ERR(spark_fan_hwmon)) {
		pr_err("spark_fan: hwmon registration failed: %ld\n",
		       (long)PTR_ERR(spark_fan_hwmon));
		thermal_cooling_device_unregister(spark_fan_cdev);
		return PTR_ERR(spark_fan_hwmon);
	}
	if (rpm_floor && !allow_pin) {
		pr_warn("spark_fan: rpm_floor asked for %u rpm without allow_pin=1; refusing\n", rpm_floor);
	} else if (rpm_floor) {
		int ret = spark_fan_write_bytes(SPARK_FAN_COMMAND_BASE + 0, rpm_floor & 0xff);

		if (!ret)
			ret = spark_fan_write_bytes(SPARK_FAN_COMMAND_BASE + 1, (rpm_floor >> 8) & 0xff);
		if (!ret)
			ret = spark_fan_write_bytes(SPARK_FAN_COMMAND_BASE + 2, rpm_floor & 0xff);
		if (!ret)
			ret = spark_fan_write_bytes(SPARK_FAN_COMMAND_BASE + 3, (rpm_floor >> 8) & 0xff);
		pr_info("spark_fan: rpm_floor %u -> %s\n", rpm_floor, ret ? "refused" : "written");
	}
	pr_info("spark_fan: level 0..100 over SMCCC %#lx (endpoint %#lx), hwmon pwm1 0..%d\n",
		(unsigned long)SPARK_FAN_FUNCTION, (unsigned long)SPARK_FAN_ENDPOINT,
		SPARK_FAN_PWM_MAX);
	return 0;
}

static void __exit spark_fan_exit(void)
{
	if (!IS_ERR_OR_NULL(spark_fan_hwmon))
		hwmon_device_unregister(spark_fan_hwmon);
	if (!IS_ERR_OR_NULL(spark_fan_cdev))
		thermal_cooling_device_unregister(spark_fan_cdev);
}

module_init(spark_fan_init);
module_exit(spark_fan_exit);

MODULE_DESCRIPTION("DGX Spark fan control over the firmware's own SMCCC service");
MODULE_LICENSE("GPL");
MODULE_AUTHOR("othexmr");

#endif /* !SPARK_FAN_HOST_TEST */
