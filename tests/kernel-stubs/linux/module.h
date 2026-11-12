/* A stub of the kernel API surface `kernel/spark_fan.c` uses, so the kernel-facing half can be
 * syntax-checked without the vendor's kernel headers.
 *
 * This is a test double, not a kernel header: every declaration here exists to make a name visible
 * to the compiler. It catches the class of defect that a host-side encoder test cannot — an
 * identifier used but never declared — and proves nothing about kernel behaviour. */
#ifndef SPARK_KERNEL_STUB_MODULE_H
#define SPARK_KERNEL_STUB_MODULE_H

#include <stdbool.h>
#include <stddef.h>

typedef unsigned long long u64;
typedef unsigned int u32;

#define __init
#define __exit

struct device;
struct module;

struct thermal_cooling_device;
struct thermal_cooling_device_ops {
	int (*get_max_state)(struct thermal_cooling_device *, unsigned long *);
	int (*get_cur_state)(struct thermal_cooling_device *, unsigned long *);
	int (*set_cur_state)(struct thermal_cooling_device *, unsigned long);
};
struct thermal_cooling_device *thermal_cooling_device_register(const char *, void *,
							      const struct thermal_cooling_device_ops *);
void thermal_cooling_device_unregister(struct thermal_cooling_device *);

/* the errno values the module returns */
#define EIO 5
#define EPERM 1

#define IS_ERR(ptr) (0)
#define IS_ERR_OR_NULL(ptr) ((ptr) == 0)
#define PTR_ERR(ptr) 0

#define pr_info(fmt, ...) do { (void)(fmt); } while (0)
#define pr_warn(fmt, ...) do { (void)(fmt); } while (0)
#define pr_err(fmt, ...) do { (void)(fmt); } while (0)

#define MODULE_DESCRIPTION(text) extern const char *__stub_description
#define MODULE_LICENSE(text) extern const char *__stub_license
#define MODULE_AUTHOR(text) extern const char *__stub_author

#define module_init(fn) static int (*__stub_init)(void) __attribute__((unused)) = fn
#define module_exit(fn) static void (*__stub_exit)(void) __attribute__((unused)) = fn

#endif
