/* SPDX-License-Identifier: Apache-2.0
 * The SPBM power-limit window, as the firmware's interface table describes it.
 *
 * The vendor firmware builds its own ACPI tables, so a window this profile maps is not necessarily
 * in the guest's memory map — and the firmware's own SMC filter drops function ids it does not know,
 * which is why a fresh id never arrives. The documented fields are therefore served on the OEM
 * function id the firmware already forwards (0xC400008D, endpoint 0x8003) under a lab GUID of its
 * own, in the same request shape the RTC and fan services use.
 */
#ifndef SPARK_SPBM_H
#define SPARK_SPBM_H

typedef unsigned long spbm_u64;

/* Field offsets, taken from research/spbm-interface.json (the vendor DSDT's own table). */
#define SPBM_PL1_OS 0x100
#define SPBM_PL2_OS 0x104
#define SPBM_PL3_OS 0x108
#define SPBM_PL4_OS 0x10c
#define SPBM_PL1_EFFECTIVE 0x160
#define SPBM_WINDOW 0x400

/* Served on 0xC400008D; the request word packs: byte 0 = op (0 read, 1 write), bytes 1..4 = the
 * field offset, bytes 5..6 = the value. The answer comes back in the same register, as the other
 * OEM services do. */
int spbm_request(spbm_u64 *r);

#endif
