/* SPDX-License-Identifier: Apache-2.0 */
#ifndef SPARK_NOR_H
#define SPARK_NOR_H

/* The board's boot flash is a 64 MiB part (MX77U51255FZ4I42 in WSON-8; the SPI
 * NOR driver in the firmware image only carries 64 MiB geometry). The default
 * stays at the 32 MiB model every stored run was taken on so those hashes remain
 * valid; build with -DNOR_SIZE=0x4000000UL for the 64 MiB arm. */
#ifndef NOR_SIZE
#define NOR_SIZE 0x2000000UL
#endif
/* Offset of the modelled flash region inside the device. The lab model has
 * always carried the variable partition alone, starting at flash 0x03000000;
 * -DNOR_OFFSET=0 maps the whole device instead, so code and store share one
 * image and a write to the code region is visible to the next boot. */
#ifndef NOR_OFFSET
#define NOR_OFFSET 0x3000000UL
#endif
#define NOR_BLOCK 4096UL
/* The flash lives in two windows. `NOR_SEED_BASE` is what the harness loads with -device loader, and
 * QEMU re-applies that blob on every machine reset, so anything written there is lost at the next
 * reset. `NOR_LIVE_BASE` is plain RAM that no loader owns: it survives a reset the way the part does,
 * which is what makes "the pages committed before a reset stay written" true rather than stated.
 * `NOR_STATE_BASE` holds the one page that marks the live window as seeded. */
#define NOR_SEED_BASE 0xa0000000UL
#define NOR_STATE_BASE (NOR_SEED_BASE + NOR_SIZE)
#define NOR_LIVE_BASE (NOR_STATE_BASE + NOR_BLOCK)
#define NOR_SEEDED 0x535041524b4e4f52UL /* "SPARKNOR" */
/* Top of the RAM the loader tag describes. The harness passes the same value it puts in the tag,
 * so a larger allocation does not advertise memory the FF-A path then refuses to map. */
#ifndef RAM_TOP
#define RAM_TOP 0xc0000000UL
#endif
/* Program granularity. A real part programs one page per command pulse, so the
 * model commits a page, then spends that page's program time. A reset that
 * lands during the pulse leaves the pages already committed written and the
 * rest untouched — the shape a power loss leaves behind. With NOR_WRITE_US 0
 * (the default) the bytes are identical to committing the request in one go. */
#define NOR_PAGE 256UL

/* Reply status byte, written to message[5]. 0x0A is the deny code the board's
 * own flash service returns to a non-trusted sender once its latch is armed —
 * measured on hardware, where every command, both tokens and both buffers answer
 * 0x0A in the default gated state. */
#define NOR_STATUS_OK 0
#define NOR_STATUS_ERROR 1
#define NOR_STATUS_DENY 0x0A

/* Access policy, decoded from the board's router: dispatch happens when the
 * latch is clear, or when the sender is trusted (source_id 0x8004). Command 8
 * is trusted-only and arms the latch, which is never cleared. */
void nor_set_policy_armed(int armed);
int nor_policy_armed(void);

/* Operate on a bounded FF-A flash message and a caller-owned NOR image.
 * `trusted` is nonzero only for the secure endpoint the board accepts. */
int nor_request(unsigned char *flash, unsigned long flash_size, unsigned char *message,
                unsigned long message_size, int trusted);

#endif
