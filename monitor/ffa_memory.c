/* SPDX-License-Identifier: Apache-2.0
 * Original bounded FF-A memory-sharing model; no vendor code or data.
 */
#include "ffa_memory.h"

typedef unsigned long u64;
typedef unsigned int u32;
typedef unsigned short u16;

/* Matches the synthetic loader's RAM map. The pinned manifest's 128 KiB
 * stmmns-memory carve-out is NS; the rest of MMAP_SECURE is not NS-owned.
 */
int ffa_ns_buffer(u64 address, u64 size) {
    return size && address >= 0x80100000UL && size <= 0x3ff00000UL &&
           address <= 0xc0000000UL - size &&
           (address + size <= 0x93400000UL || address >= 0x94240000UL) &&
           (address + size <= 0xa0000000UL || address >= 0xa0100000UL);
}

static int permissions(unsigned value) {
    /* Normal data memory: RO/RW, instruction access unspecified or NX.
     * This subset never grants executable mappings.
     */
    return value == 1 || value == 2 || value == 5 || value == 6;
}

static int header(const u64 *d) {
    return (u32)d[3] == 16 && d[3] >> 32 == 1 && d[4] == 48 && !d[5];
}

int ffa_memory_share(struct ffa_memory *state, const u64 *d, const u64 mailboxes[4]) {
    u64 address = d[10], pages = (u32)d[11], size = pages * 4096;
    unsigned permission = (d[6] >> 16) & 255;
    if (!header(d) || d[0] >> 16 != 0x2f || d[1] ||
        d[6] >> 32 != 64 || (u16)d[6] != 0x8004 ||
        (d[6] >> 24 & 255) || !permissions(permission) || d[7] ||
        !pages || pages > 32 || d[11] != pages ||
        d[8] != (1UL << 32 | pages) || d[9] || (address & 4095))
        return FFA_INVALID_PARAMETERS;
    if ((u16)d[0] || !ffa_ns_buffer(address, size))
        return FFA_DENIED;
    for (unsigned i = 0; i < 4; i++) {
        if (mailboxes[i] && address < mailboxes[i] + 4096 && address + size > mailboxes[i])
            return FFA_DENIED;
    }
    if (state->handle || state->next_handle == 0x7fffffffffffffffUL)
        return FFA_NO_MEMORY;
    /* Handle 1 remains private to the existing synchronous flash model.
     * Never recycle an NS handle, so a stale handle cannot name a new share.
     */
    if (!state->next_handle)
        state->next_handle = 1;
    state->handle = ++state->next_handle;
    state->retrieved = 0;
    for (unsigned i = 0; i < 12; i++)
        state->descriptor[i] = d[i];
    state->descriptor[1] = state->handle;
    return 0;
}

int ffa_memory_retrieve(struct ffa_memory *state, const u64 *d, u64 *response) {
    unsigned permission = (d[6] >> 16) & 255;
    unsigned granted = (state->descriptor[6] >> 16) & 255;
    /* Retrieve omits the composite region; the relayer supplies the stored
     * constituent in the response, not a caller-selected address.
     */
    if (!header(d) ||
        ((d[0] >> 16 & 65535) != 0 && (d[0] >> 16 & 65535) != 0x2f) ||
        (d[0] >> 32 != 0 && d[0] >> 32 != 8) ||
        d[6] >> 32 || (u16)d[6] != 0x8004 || (d[6] >> 24 & 255) ||
        (permission && permission != 4 && !permissions(permission)) || d[7])
        return FFA_INVALID_PARAMETERS;
    if ((u16)d[0])
        return FFA_DENIED;
    if (!state->handle || d[1] != state->handle || d[2] != state->descriptor[2])
        return FFA_INVALID_PARAMETERS;
    if (state->retrieved || ((permission & 3) == 2 && (granted & 3) == 1))
        return FFA_DENIED;
    if (!(permission & 3))
        permission = granted;
    for (unsigned i = 0; i < 12; i++)
        response[i] = state->descriptor[i];
    /* SHARE type, normal WB/inner-shareable, NS attribute, RO/RW and NX. */
    response[0] = (8UL << 32) | (0x6fUL << 16);
    response[6] = (64UL << 32) | ((u64)((permission & 3) | 4) << 16) | 0x8004;
    state->retrieved = 1;
    return 0;
}

int ffa_memory_relinquish(struct ffa_memory *state, const u64 *d) {
    if (!state->handle || d[0] != state->handle || d[1] != 1UL << 32 ||
        (u16)d[2] != 0x8004)
        return FFA_INVALID_PARAMETERS;
    if (!state->retrieved)
        return FFA_DENIED;
    state->retrieved = 0;
    return 0;
}

int ffa_memory_reclaim(struct ffa_memory *state, u64 handle) {
    if (!state->handle || handle != state->handle)
        return FFA_INVALID_PARAMETERS;
    if (state->retrieved)
        return FFA_DENIED;
    state->handle = 0;
    for (unsigned i = 0; i < 12; i++)
        state->descriptor[i] = 0;
    return 0;
}
