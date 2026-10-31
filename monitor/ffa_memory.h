/* SPDX-License-Identifier: Apache-2.0 */
#ifndef SPARK_FFA_MEMORY_H
#define SPARK_FFA_MEMORY_H

#define FFA_INVALID_PARAMETERS (-2)
#define FFA_NO_MEMORY (-3)
#define FFA_BUSY (-4)
#define FFA_DENIED (-6)

/* One NS -> STMM transaction, independent of STMM's flash-service share.
 * FF-A v1.1 descriptor layout: one receiver and one contiguous constituent.
 */
struct ffa_memory {
    unsigned long descriptor[12];
    unsigned long handle, next_handle;
    int retrieved;
};

int ffa_ns_buffer(unsigned long address, unsigned long size);
int ffa_memory_share(struct ffa_memory *state, const unsigned long *descriptor,
                     const unsigned long mailboxes[4]);
int ffa_memory_retrieve(struct ffa_memory *state, const unsigned long *request,
                        unsigned long *response);
int ffa_memory_relinquish(struct ffa_memory *state, const unsigned long *request);
int ffa_memory_reclaim(struct ffa_memory *state, unsigned long handle);

#endif
