/* SPDX-License-Identifier: Apache-2.0
 * Functional variable-partition flash service; no vendor implementation code.
 */
#include "nor.h"

static unsigned long get_le(const unsigned char *p, unsigned count) {
    unsigned long value = 0;
    for (unsigned i = 0; i < count; i++)
        value |= (unsigned long)p[i] << (8 * i);
    return value;
}

static void put_le(unsigned char *p, unsigned long value, unsigned count) {
    for (unsigned i = 0; i < count; i++)
        p[i] = value >> (8 * i);
}

static int contains(unsigned long address, unsigned long size) {
    return address >= NOR_OFFSET && size <= NOR_SIZE && address - NOR_OFFSET <= NOR_SIZE - size;
}

int nor_request(unsigned char *flash, unsigned long flash_size, unsigned char *b,
                unsigned long size) {
    if (flash_size != NOR_SIZE || size < 80)
        return 0;
    unsigned cmd = b[0], subcommand = b[5], error = 0;
    unsigned long request = get_le(b + 1, 4), address = get_le(b + 7, 8);
    unsigned long count = get_le(b + 32, 4), response = 0;
    if (request > size - 32)
        return 0;

    if (cmd == 3 && subcommand == 3 && request == 0) {
        response = 33;
        for (unsigned i = 32; i < 80; i++)
            b[i] = 0;
        b[32] = 1;
        put_le(b + 0x31, NOR_OFFSET, 8);
        put_le(b + 0x39, NOR_SIZE, 8);
    } else if (cmd == 0 && request == 4 && count && count <= NOR_BLOCK && count <= size - 32 &&
               contains(address, count)) {
        response = count;
        for (unsigned long i = 0; i < count; i++)
            b[32 + i] = flash[address - NOR_OFFSET + i];
    } else if (cmd == 1 && request && request <= NOR_BLOCK && contains(address, request)) {
        unsigned char *target = flash + address - NOR_OFFSET;
        /* Validate the entire request before changing any bytes. */
        for (unsigned long i = 0; i < request; i++)
            if ((target[i] & b[32 + i]) != b[32 + i])
                error = 1;
        if (!error)
            for (unsigned long i = 0; i < request; i++)
                target[i] &= b[32 + i];
    } else if (cmd == 2 && request == 4 && count && !(count & (NOR_BLOCK - 1)) &&
               !(address & (NOR_BLOCK - 1)) && contains(address, count)) {
        for (unsigned long i = 0; i < count; i++)
            flash[address - NOR_OFFSET + i] = 255;
    } else if (cmd != 8 || request != 0) {
        error = 1;
    }
    /* RAM-backed writes complete synchronously; FLUSH has no pending work. */
    for (unsigned i = 0; i < 32; i++)
        b[i] = 0;
    b[0] = cmd;
    b[5] = error;
    put_le(b + 1, response, 4);
    return 1;
}
