/* The errno values `kernel/spark_fan.c` returns, with the numbers the kernel uses. A test double:
 * only their existence and distinctness matter to a syntax check. */
#ifndef SPARK_KERNEL_STUB_ERRNO_H
#define SPARK_KERNEL_STUB_ERRNO_H

#include "module.h"

#define EPERM        1
#define EIO          5
#define EINVAL      22
#define EOPNOTSUPP  95

#endif
