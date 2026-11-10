#ifndef SPARK_KERNEL_STUB_MODULEPARAM_H
#define SPARK_KERNEL_STUB_MODULEPARAM_H
/* Parameters are declared, not wired: this stub exists so the compiler sees the name and the type. */
#define module_param(name, type, perm) extern int __stub_param_##name
#define MODULE_PARM_DESC(name, desc) extern const char *__stub_desc_##name
#endif
