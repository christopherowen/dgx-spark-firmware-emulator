/* SPDX-License-Identifier: Apache-2.0
 * TCG plugin: log every execution of one instruction, with the registers it touched.
 *
 * It observes the guest from inside QEMU, with no breakpoints and no single-stepping, so the
 * instruction stream is the one the guest would run anyway. The lab used a copy of this to sample
 * a firmware walk; this is the original, maintained here so a clean clone does not need the lab.
 *
 * Options, after `-plugin file=…`:
 *   pc=0xADDR    instruction to watch (required)
 *   limit=N      stop logging after N hits; 0 (the default) means no limit — every hit is logged
 *                and the summary still reports the total, so a cap is always a deliberate choice
 *   regs=x4,x0   registers to print with each hit (default x4,x0)
 *   start=N      do not sample before the N-th executed block; lets a sampler skip a firmware phase
 *                whose block count is already known from a previous run's summary
 *   sample=N     log the address of every N-th translated basic block executed; 0 (the default)
 *                turns it off. With pc left at 0 this is a sampler: it answers "where is the guest
 *                running now?" for a boot that never reaches its next print, which no counter can.
 *
 * Every number in the output is a *count*, and the final line says whether the limit cut the
 * observation: a capped trace is censored evidence, and a reader must be able to tell. With no
 * limit the summary reports `censored=0` and `printed == hits`.
 *
 * Build: see tools/build_plugin.py.
 */
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <qemu-plugin.h>

QEMU_PLUGIN_EXPORT int qemu_plugin_version = QEMU_PLUGIN_VERSION;

#define MAX_REGS 8

static uint64_t target_pc;
static uint64_t limit = 0;
static uint64_t hits;
static uint64_t printed;
#define MAX_CPUS 16
static uint64_t per_cpu[MAX_CPUS];
static int capped;
static const char *wanted[MAX_REGS];
static int wanted_count;
static qemu_plugin_reg_descriptor *resolved[MAX_REGS];
static GArray *register_table;
static uint64_t sample_every;
static uint64_t sample_start;
static uint64_t blocks;
static uint64_t samples;

static void on_tb_exec(unsigned int cpu_index, void *userdata)
{
    uint64_t block = blocks++;
    if (!sample_every || block < sample_start || (block - sample_start) % sample_every) {
        return;
    }
    samples++;
    fprintf(stderr, "SAMPLE %06" PRIu64 " cpu=%u pc=%#" PRIx64 "\n",
            samples, cpu_index, (uint64_t)(uintptr_t)userdata);
    if (samples % 200 == 0) {
        fflush(stderr);
    }
}

static void resolve_registers(unsigned int cpu_index)
{
    (void)cpu_index;
    if (register_table) {
        return;
    }
    register_table = qemu_plugin_get_registers();
    if (!register_table) {
        return;
    }
    for (int want = 0; want < wanted_count; want++) {
        for (guint i = 0; i < register_table->len; i++) {
            qemu_plugin_reg_descriptor *entry =
                &g_array_index(register_table, qemu_plugin_reg_descriptor, i);
            if (entry->name && strcmp(entry->name, wanted[want]) == 0) {
                resolved[want] = entry;
                break;
            }
        }
    }
}

static uint64_t read_resolved(int index)
{
    uint64_t value = 0;
    if (!resolved[index]) {
        return 0;
    }
    GByteArray *buffer = g_byte_array_new();
    if (qemu_plugin_read_register(resolved[index]->handle, buffer) && buffer->len >= 8) {
        memcpy(&value, buffer->data, 8);
    }
    g_byte_array_free(buffer, TRUE);
    return value;
}

static void on_exec(unsigned int cpu_index, void *userdata)
{
    (void)userdata;
    uint64_t index = hits++;
    if (cpu_index < MAX_CPUS)
        per_cpu[cpu_index]++;
    if (limit && index >= limit) {
        if (!capped) {
            /* Say it once, at the moment the observation becomes censored: a reader must be able
             * to tell a capped trace from a short one without knowing the configured limit. */
            capped = 1;
            /* The counts travel with the announcement: a run that is killed before its summary is
             * still measurable, and still labelled censored. */
            fprintf(stderr, "TRACE capped hits=%" PRIu64 " printed=%" PRIu64 " limit=%" PRIu64
                            "; later executions are counted but not logged\n", hits, printed, limit);
            fflush(stderr);
        }
        return;
    }
    resolve_registers(cpu_index);
    fprintf(stderr, "TRACE hit %06" PRIu64 " cpu=%u", index, cpu_index);
    for (int i = 0; i < wanted_count; i++)
        fprintf(stderr, " %s=%" PRIx64, wanted[i], read_resolved(i));
    fputc('\n', stderr);
    printed++;
    if (printed % 1000 == 0) {
        /* A checkpoint, so a trace whose guest never shuts down cleanly still carries counts: a
         * killed run is otherwise unmeasured rather than censored. */
        fprintf(stderr, "TRACE checkpoint hits=%" PRIu64 " printed=%" PRIu64 "\n", hits, printed);
        fflush(stderr);
    }
}

static void on_tb_trans(struct qemu_plugin_tb *tb, void *userdata)
{
    (void)userdata;
    size_t count = qemu_plugin_tb_n_insns(tb);
    if (sample_every && count) {
        uint64_t start = qemu_plugin_insn_vaddr(qemu_plugin_tb_get_insn(tb, 0));
        qemu_plugin_register_vcpu_tb_exec_cb(tb, on_tb_exec, QEMU_PLUGIN_CB_NO_REGS,
                                            (void *)(uintptr_t)start);
    }
    for (size_t i = 0; i < count; i++) {
        struct qemu_plugin_insn *instruction = qemu_plugin_tb_get_insn(tb, i);
        if (target_pc && qemu_plugin_insn_vaddr(instruction) == target_pc) {
            qemu_plugin_register_vcpu_insn_exec_cb(instruction, on_exec, QEMU_PLUGIN_CB_R_REGS, NULL);
        }
    }
}

static void on_finish(void *userdata)
{
    (void)userdata;
    fprintf(stderr, "TRACE summary pc=%#" PRIx64 " hits=%" PRIu64 " printed=%" PRIu64
                    " limit=%" PRIu64 " censored=%d blocks=%" PRIu64 " samples=%" PRIu64,
            target_pc, hits, printed, limit, capped ? 1 : 0, blocks, samples);
    for (unsigned cpu = 0; cpu < MAX_CPUS; cpu++)
        if (per_cpu[cpu])
            fprintf(stderr, " cpu%u=%" PRIu64, cpu, per_cpu[cpu]);
    fputc('\n', stderr);
    fflush(stderr);
}

QEMU_PLUGIN_EXPORT int qemu_plugin_install(qemu_plugin_id_t id, const qemu_info_t *info,
                                           int argc, char **argv)
{
    (void)info;
    wanted[0] = "x4";
    wanted[1] = "x0";
    wanted_count = 2;
    for (int i = 0; i < argc; i++) {
        if (strncmp(argv[i], "pc=", 3) == 0) {
            target_pc = strtoull(argv[i] + 3, NULL, 0);
        } else if (strncmp(argv[i], "limit=", 6) == 0) {
            limit = strtoull(argv[i] + 6, NULL, 0);
        } else if (strncmp(argv[i], "start=", 6) == 0) {
            sample_start = strtoull(argv[i] + 6, NULL, 0);
        } else if (strncmp(argv[i], "sample=", 7) == 0) {
            sample_every = strtoull(argv[i] + 7, NULL, 0);
        } else if (strncmp(argv[i], "regs=", 5) == 0) {
            wanted_count = 0;
            char *copy = g_strdup(argv[i] + 5);
            for (char *token = strtok(copy, ","); token && wanted_count < MAX_REGS;
                 token = strtok(NULL, ","))
                wanted[wanted_count++] = g_strdup(token);
            g_free(copy);
        }
    }
    if (!target_pc && !sample_every) {
        fprintf(stderr, "TRACE error: pwatching needs pc= or sample=; one of them is required\n");
        return -1;
    }
    qemu_plugin_register_vcpu_tb_trans_cb(id, on_tb_trans, NULL);
    qemu_plugin_register_atexit_cb(id, on_finish, NULL);
    fprintf(stderr, "TRACE watching %#" PRIx64 " limit=%" PRIu64 " regs=%d sample_every=%" PRIu64
                    "\n", target_pc, limit, wanted_count, sample_every);
    return 0;
}
