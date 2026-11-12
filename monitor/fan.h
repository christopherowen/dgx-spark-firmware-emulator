/* SPDX-License-Identifier: Apache-2.0
 * The fan service behind SMCCC 0xC400008D, modelled from the firmware's own DSDT.
 *
 * It lives in its own translation unit so it can be driven from a host test with the exact
 * requests the DSDT builds, and so the monitor stays a dispatcher rather than a home for device
 * models. Nothing here is the board's EC: the EC is not modelled, and the arithmetic is the
 * firmware's, taken from \_SB.FAN0.WFCF.
 */
#ifndef SPARK_FAN_H
#define SPARK_FAN_H

/* The monitor's u64 is unsigned long on this target; the model takes the same type. */
typedef unsigned long fan_u64;

/* FMIN/FMAX as the DSDT declares them for FAN0. */
#define FAN_RPM_MIN 975UL
#define FAN_RPM_MAX 4350UL

/* Serve one request, in the register shape the SMC hands over. Returns 1 when this service
 * answered (the caller then returns), 0 when the request belongs to someone else. */
int fan_request(fan_u64 *r);

/* The modelled rpm of a fan, for tests and diagnostics. */
unsigned fan_rpm(unsigned index);

/* The rpm the firmware's WFCF computes for a level 0..100. */
unsigned fan_rpm_for_level(unsigned level);

#ifdef SPARK_FFA_EC
int fan_packet_request(fan_u64 *r);
unsigned fan_lower_floor(void);
unsigned fan_packet_rpm(unsigned index);
/* Applied duty as rational percent, not a claim about physical PWM resolution. */
unsigned fan_duty(unsigned index);
unsigned fan_duty_denominator(unsigned index);
/* Recovered EC published estimate; duty is numerator/denominator percent. */
unsigned fan_rpm_for_duty(unsigned index, unsigned numerator, unsigned denominator);
#endif

#endif
