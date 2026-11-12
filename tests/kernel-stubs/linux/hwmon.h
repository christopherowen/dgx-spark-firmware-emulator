/* A stub of the hwmon API surface `kernel/spark_fan.c` uses, so the kernel-facing half can be
 * syntax-checked without the vendor's kernel headers.
 *
 * This is a test double, not a kernel header. It exists to make the names the module uses visible to
 * the compiler with the right arities — an identifier used but never declared is exactly the defect
 * `tests/test_kernel_module_syntax.py` was written for — and it proves nothing about kernel behaviour. */
#ifndef SPARK_KERNEL_STUB_HWMON_H
#define SPARK_KERNEL_STUB_HWMON_H

#include "module.h"

typedef unsigned short umode_t;

enum hwmon_sensor_types {
	hwmon_chip = 0,
	hwmon_temp = 1,
	hwmon_fan = 4,
	hwmon_pwm = 5,
};

/* The attribute ids the module names. Their values are the kernel's, but only their existence and
 * distinctness matter to a syntax check. */
#define hwmon_chip_update_interval 0
#define hwmon_fan_input   0
#define hwmon_pwm_input   0
#define hwmon_pwm_enable  1

#define HWMON_F_INPUT     0
#define HWMON_PWM_INPUT   0
#define HWMON_PWM_ENABLE  1

/* HWMON_CHANNEL_INFO(type, ...) expands to the kernel's channel-info struct; here it only has to
 * accept the same argument list. */
/* Accepts the real argument list and produces a channel-info pointer; the designators the kernel
 * fills in are not needed to check that the module's own code compiles. */
#define HWMON_CHANNEL_INFO(type, ...) (&(const struct hwmon_channel_info){ 0 })
struct hwmon_channel_info {
	enum hwmon_sensor_types type;
};

struct hwmon_ops {
	umode_t (*is_visible)(const void *, enum hwmon_sensor_types, u32, int);
	int (*read)(struct device *, enum hwmon_sensor_types, u32, int, long *);
	int (*write)(struct device *, enum hwmon_sensor_types, u32, int, long);
};

struct hwmon_chip_info {
	const struct hwmon_ops *ops;
	const struct hwmon_channel_info *const *info;
};

struct device *hwmon_device_register_with_info(struct device *, const char *, void *,
					       const struct hwmon_chip_info *, const void *);
void hwmon_device_unregister(struct device *);

#endif
