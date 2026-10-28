/* SPDX-License-Identifier: Apache-2.0 */
#ifndef SPARK_NOR_H
#define SPARK_NOR_H

#define NOR_SIZE 0x100000UL
#define NOR_OFFSET 0x3000000UL
#define NOR_BLOCK 4096UL

/* Operate on a bounded FF-A flash message and a caller-owned NOR image. */
int nor_request(unsigned char *flash, unsigned long flash_size, unsigned char *message,
                unsigned long message_size);

#endif
