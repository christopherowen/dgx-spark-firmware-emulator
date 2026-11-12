/* SPDX-License-Identifier: Apache-2.0
 * Run the real probe's guards and encoders; replace only the privileged SMC transport.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static unsigned calls;
static unsigned long long request[5];
static unsigned long long call(unsigned long long *frame)
{
    calls++;
    memcpy(request, frame, sizeof(request));
    frame[0] = 0xc400008e;
    frame[4] = 1978;
    return frame[0];
}

#define SPARK_FAN_SMC_H
#define _start probe_start
#define putc probe_putc
#include "fan_probe.c"
#undef putc
#undef _start

int main(int argc, char **argv)
{
    if (argc != 4) return 2;
    u64 command = strtoull(argv[2], 0, 0), value = strtoull(argv[3], 0, 0);
    u64 rpm = 0;
    int accepted;
    if (!strcmp(argv[1], "read")) accepted = read_fan(command, &rpm);
    else if (!strcmp(argv[1], "write")) accepted = write_byte(command, value);
    else return 2;
    printf("{\"accepted\":%d,\"calls\":%u,\"rpm\":%llu,\"frame\":[", accepted, calls, rpm);
    for (unsigned i = 0; i < 5; i++) printf("%s%llu", i ? "," : "", request[i]);
    printf("]}\n");
    return 0;
}
