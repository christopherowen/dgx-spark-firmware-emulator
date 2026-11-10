#ifndef SPARK_KERNEL_STUB_ARM_SMCCC_H
#define SPARK_KERNEL_STUB_ARM_SMCCC_H
/* Declared so the file's include list type-checks. The module deliberately does NOT use
 * arm_smccc_smc(): Linux's result struct stops at a3 and the answer comes back in x4. */
struct arm_smccc_res { unsigned long a0, a1, a2, a3; };
static inline void arm_smccc_smc(unsigned long a0, unsigned long a1, unsigned long a2,
				 unsigned long a3, unsigned long a4, unsigned long a5,
				 unsigned long a6, unsigned long a7, struct arm_smccc_res *res)
{
	(void)a0; (void)a1; (void)a2; (void)a3; (void)a4; (void)a5; (void)a6; (void)a7;
	if (res) { res->a0 = 0; res->a1 = 0; res->a2 = 0; res->a3 = 0; }
}
#endif
