// Gross144 streamed S2R CPU tail.
//
// Standalone C11 worker.  No floating point, no per-shot graph expansion,
// no Python/NumPy allocation in hot loop.  Image is quotient-compiled by
// export_paper_gross144_c_tail.py.  Protocol: JSONL request with syndrome_hex;
// JSONL response with exact syndrome/logical result.

#define _POSIX_C_SOURCE 200809L
#include <stdint.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#ifdef _OPENMP
#include <omp.h>
#endif

#define MAGIC "MTNCV1\0"
#define MAX_CONFIGS 9
#define DEFAULT_PORTFOLIO_LIMIT 4
#define CHECKS 1728
#define GROUP_ORDER 72
#define LOGICALS 12
#define RELAY_LITE_SETS 32
#define RELAY_LITE_FALLBACK_SETS 240
#define RELAY_LITE_PRE 10
#define RELAY_LITE_SET_ITERS 50
#define RELAY_LITE_STOP 1
#define RELAY_PRE_MAX 120
#define RELAY_SET_MAX 100
#define RELAY_STOP_MAX 3
#define RELAY_SET_COUNT_MAX 240
#define RELAY_LITE_PRIMARY_SEED 43091U
#define RELAY_LITE_ESCAPE_SEED 1U

// Defaults are the validated fast rescue. Runtime limits remain available for
// reproducible stress and ablation runs; the independent escape leg plus
// three-way quorum protects ambiguous portfolio cases.
static uint32_t g_relay_lite_sets = RELAY_LITE_SETS;
static uint32_t g_relay_lite_fallback_sets = RELAY_LITE_FALLBACK_SETS;
static uint32_t g_relay_lite_pre = RELAY_LITE_PRE;
static uint32_t g_relay_lite_set_iters = RELAY_LITE_SET_ITERS;
static uint32_t g_relay_lite_stop = RELAY_LITE_STOP;
static int g_skip_relay = 0;
// Diagnostic-only selectors used to isolate the two retained-posterior
// rescue streams. They are never enabled by the normal Python launcher.
static int g_relay_only = 0;
static int g_relay_escape_only = 0;
static int16_t g_trunc_div128[4096];

typedef struct {
    uint32_t max_iterations;
    uint32_t memory_weight_shift;
    uint32_t message_bits;
    int32_t dither_amplitude;
    uint32_t dither_seed;
    int32_t memory_low_q;
    int32_t memory_high_q;
    uint32_t memory_seed;
    int has_memory_q;
} Config;

typedef struct {
    uint32_t variables, checks, edges, group_order, slices, orbits;
    uint32_t dict_count, label_template_count, order_count, bridge_rank;
    int16_t *orbit_priors;
    uint8_t *orbit_start, *orbit_end;
    uint32_t *offsets, *edge_faults;
    uint32_t *fault_offsets, *fault_edges, *fault_checks;
    uint32_t *template_offsets;
    uint16_t *template_orbits;
    uint8_t *template_anchors;
    uint16_t *logical_dictionary;
    uint8_t *label_templates;
    uint16_t *orbit_label_template_ids;
    int32_t *completion_parents, *completion_faults;
    uint32_t *completion_order;
    int32_t *component_by_check;
    uint32_t *bridge_faults;
    uint64_t *bridge_vectors_lo, *bridge_vectors_hi;
    uint64_t *bridge_combinations_lo, *bridge_combinations_hi;
} Image;

typedef struct {
    int16_t *prior, *prior_div128, *posterior;
    int32_t *summed;
    int16_t *messages, *memory_q;
    uint16_t *first, *second, *first_count;
    uint8_t *parity, *correction, *syndrome, *residual, *state;
    int32_t *flips;
} Scratch;

typedef struct {
    int accepted;
    int syndrome_exact;
    uint16_t logical;
    int32_t weight;
    int64_t prior_cost;
    uint32_t iterations;
    uint32_t sets_attempted;
    uint32_t stage_reached;
    const char *reason;
} Result;

typedef struct {
    uint64_t portfolio_ns;
    uint64_t selection_ns;
    uint64_t relay_primary_ns;
    uint64_t relay_escape_ns;
    uint32_t relay_runs;
    uint32_t portfolio_configs;
    uint32_t relay_primary_iterations;
    uint32_t relay_escape_iterations;
    uint32_t relay_primary_sets;
    uint32_t relay_escape_sets;
    uint32_t relay_primary_stage;
    uint32_t relay_escape_stage;
    int logical_disagreement;
} RequestTiming;

static uint64_t now_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
}

static int read_exact(FILE *f, void *dst, size_t n) {
    return fread(dst, 1, n, f) == n;
}

static void free_image(Image *im) {
    free(im->orbit_priors); free(im->orbit_start); free(im->orbit_end);
    free(im->offsets); free(im->edge_faults);
    free(im->fault_offsets); free(im->fault_edges); free(im->fault_checks);
    free(im->template_offsets); free(im->template_orbits);
    free(im->template_anchors); free(im->logical_dictionary);
    free(im->label_templates); free(im->orbit_label_template_ids);
    free(im->completion_parents); free(im->completion_faults);
    free(im->completion_order); free(im->component_by_check);
    free(im->bridge_faults); free(im->bridge_vectors_lo);
    free(im->bridge_vectors_hi); free(im->bridge_combinations_lo);
    free(im->bridge_combinations_hi);
    memset(im, 0, sizeof(*im));
}

static int load_image(const char *path, Image *im) {
    FILE *f = fopen(path, "rb");
    char magic[8];
    uint32_t version, reserved, template_edges = 0;
    if (!f) return 0;
    memset(im, 0, sizeof(*im));
    if (!read_exact(f, magic, sizeof(magic)) || memcmp(magic, MAGIC, 8) != 0 ||
        !read_exact(f, &version, 4) || version != 1 ||
        !read_exact(f, &reserved, 4) ||
        !read_exact(f, &im->variables, 4) || !read_exact(f, &im->checks, 4) ||
        !read_exact(f, &im->edges, 4) || !read_exact(f, &im->group_order, 4) ||
        !read_exact(f, &im->slices, 4) || !read_exact(f, &im->orbits, 4) ||
        !read_exact(f, &im->dict_count, 4) ||
        !read_exact(f, &im->label_template_count, 4) ||
        !read_exact(f, &im->order_count, 4) ||
        !read_exact(f, &im->bridge_rank, 4)) {
        fclose(f); return 0;
    }
    if (im->checks != CHECKS || im->group_order != GROUP_ORDER ||
        im->slices != 24 || im->orbits == 0 || im->edges == 0) {
        fclose(f); return 0;
    }
#define ALLOC(name, count) do { \
    im->name = calloc((count), sizeof(*im->name)); \
    if (!im->name) { fclose(f); free_image(im); return 0; } \
} while (0)
    ALLOC(orbit_priors, im->orbits);
    ALLOC(orbit_start, im->orbits);
    ALLOC(orbit_end, im->orbits);
    ALLOC(offsets, im->checks + 1);
    ALLOC(edge_faults, im->edges);
    ALLOC(fault_offsets, im->variables + 1);
    ALLOC(fault_edges, im->edges);
    ALLOC(fault_checks, im->edges);
    ALLOC(template_offsets, im->slices + 1);
    ALLOC(logical_dictionary, im->dict_count);
    ALLOC(label_templates, im->label_template_count * GROUP_ORDER);
    ALLOC(orbit_label_template_ids, im->orbits);
    ALLOC(completion_parents, im->order_count);
    ALLOC(completion_faults, im->order_count);
    ALLOC(completion_order, im->order_count);
    ALLOC(component_by_check, im->checks);
    ALLOC(bridge_faults, im->bridge_rank);
    ALLOC(bridge_vectors_lo, im->bridge_rank);
    ALLOC(bridge_vectors_hi, im->bridge_rank);
    ALLOC(bridge_combinations_lo, im->bridge_rank);
    ALLOC(bridge_combinations_hi, im->bridge_rank);
#undef ALLOC
    if (!read_exact(f, im->orbit_priors, sizeof(*im->orbit_priors) * im->orbits) ||
        !read_exact(f, im->orbit_start, im->orbits) ||
        !read_exact(f, im->orbit_end, im->orbits) ||
        !read_exact(f, im->offsets, sizeof(*im->offsets) * (im->checks + 1)) ||
        !read_exact(f, im->edge_faults, sizeof(*im->edge_faults) * im->edges)) {
        fclose(f); free_image(im); return 0;
    }
    for (uint32_t e = 0; e < im->edges; ++e)
        ++im->fault_offsets[im->edge_faults[e] + 1];
    for (uint32_t v = 1; v <= im->variables; ++v)
        im->fault_offsets[v] += im->fault_offsets[v - 1];
    uint32_t *fault_cursor = calloc(im->variables, sizeof(*fault_cursor));
    if (!fault_cursor) { fclose(f); free_image(im); return 0; }
    memcpy(fault_cursor, im->fault_offsets,
           sizeof(*fault_cursor) * im->variables);
    for (uint32_t c = 0; c < im->checks; ++c)
        for (uint32_t e = im->offsets[c]; e < im->offsets[c + 1]; ++e) {
            uint32_t fault = im->edge_faults[e];
            uint32_t slot = fault_cursor[fault]++;
            im->fault_edges[slot] = e;
            im->fault_checks[slot] = c;
        }
    free(fault_cursor);
    for (uint32_t t = 0; t < im->slices; ++t) {
        uint32_t degree;
        if (!read_exact(f, &degree, 4)) { fclose(f); free_image(im); return 0; }
        im->template_offsets[t] = template_edges;
        template_edges += degree;
        im->template_offsets[t + 1] = template_edges;
    }
    im->template_orbits = calloc(template_edges, sizeof(*im->template_orbits));
    im->template_anchors = calloc(template_edges, sizeof(*im->template_anchors));
    if (!im->template_orbits || !im->template_anchors) {
        fclose(f); free_image(im); return 0;
    }
    // Degree words were read first; exporter writes pairs after all degrees.
    for (uint32_t t = 0; t < im->slices; ++t) {
        for (uint32_t j = im->template_offsets[t]; j < im->template_offsets[t + 1]; ++j) {
            if (!read_exact(f, &im->template_orbits[j], 2) ||
                !read_exact(f, &im->template_anchors[j], 1)) {
                fclose(f); free_image(im); return 0;
            }
        }
    }
    if (!read_exact(f, im->logical_dictionary, 2 * im->dict_count) ||
        !read_exact(f, im->label_templates,
                    im->label_template_count * GROUP_ORDER) ||
        !read_exact(f, im->orbit_label_template_ids,
                    2 * im->orbits) ||
        !read_exact(f, im->completion_parents,
                    4 * im->order_count) ||
        !read_exact(f, im->completion_faults, 4 * im->order_count) ||
        !read_exact(f, im->completion_order,
                    4 * im->order_count) ||
        !read_exact(f, im->component_by_check, 4 * im->checks) ||
        !read_exact(f, im->bridge_faults, 4 * im->bridge_rank) ||
        !read_exact(f, im->bridge_vectors_lo, 8 * im->bridge_rank) ||
        !read_exact(f, im->bridge_vectors_hi, 8 * im->bridge_rank) ||
        !read_exact(f, im->bridge_combinations_lo, 8 * im->bridge_rank) ||
        !read_exact(f, im->bridge_combinations_hi, 8 * im->bridge_rank)) {
        fclose(f); free_image(im); return 0;
    }
    fclose(f);
    return 1;
}

static void free_scratch(Scratch *s) {
    free(s->prior); free(s->prior_div128); free(s->posterior);
    free(s->summed); free(s->messages);
    free(s->memory_q); free(s->first); free(s->second); free(s->first_count);
    free(s->parity); free(s->correction); free(s->syndrome); free(s->residual);
    free(s->state); free(s->flips); memset(s, 0, sizeof(*s));
}

static int alloc_scratch(const Image *im, Scratch *s) {
    memset(s, 0, sizeof(*s));
    s->prior = calloc(im->variables, sizeof(*s->prior));
    s->prior_div128 = calloc(im->variables, sizeof(*s->prior_div128));
    s->posterior = calloc(im->variables, sizeof(*s->posterior));
    s->summed = calloc(im->variables, sizeof(*s->summed));
    s->messages = calloc(im->edges, sizeof(*s->messages));
    s->memory_q = calloc(im->variables, sizeof(*s->memory_q));
    s->first = calloc(im->checks, sizeof(*s->first));
    s->second = calloc(im->checks, sizeof(*s->second));
    s->first_count = calloc(im->checks, sizeof(*s->first_count));
    s->parity = calloc(im->checks, sizeof(*s->parity));
    s->correction = calloc(im->variables, sizeof(*s->correction));
    s->syndrome = calloc(im->checks, sizeof(*s->syndrome));
    s->residual = calloc(im->checks, sizeof(*s->residual));
    s->state = calloc(im->checks + 1, sizeof(*s->state));
    s->flips = calloc(im->variables, sizeof(*s->flips));
    if (!s->prior || !s->prior_div128 || !s->posterior ||
        !s->summed || !s->messages ||
        !s->memory_q || !s->first || !s->second || !s->first_count ||
        !s->parity || !s->correction || !s->syndrome || !s->residual ||
        !s->state || !s->flips) { free_scratch(s); return 0; }
    return 1;
}

static uint32_t mix32(uint32_t x) {
    x ^= x << 13; x ^= x >> 17; x ^= x << 5; return x;
}

static uint64_t mix64(uint64_t x) {
    x ^= x >> 30; x *= UINT64_C(0xBF58476D1CE4E5B9);
    x ^= x >> 27; x *= UINT64_C(0x94D049BB133111EB);
    x ^= x >> 31; return x;
}

static int64_t floor_div_i64(int64_t n, int64_t d) {
    int64_t q = n / d, r = n % d;
    return (r != 0 && n < 0) ? q - 1 : q;
}

static int64_t trunc_div_i64(int64_t n, int64_t d) {
    return n / d;
}

static inline int16_t trunc_div128_lookup(int32_t value) {
    if (value < -2048) value = -2048;
    if (value > 2047) value = 2047;
    return g_trunc_div128[value + 2048];
}

static void init_trunc_div128_table(void) {
    for (int32_t value = -2048; value <= 2047; ++value)
        g_trunc_div128[value + 2048] = (int16_t)(value / 128);
}

static void init_state(const Image *im, const Config *cfg, Scratch *s) {
    for (uint32_t orbit = 0; orbit < im->orbits; ++orbit) {
        int32_t p = im->orbit_priors[orbit];
        uint32_t base = orbit * GROUP_ORDER;
        for (uint32_t c = 0; c < GROUP_ORDER; ++c) {
            uint32_t v = base + c;
            int32_t value = p;
            if (cfg->dither_amplitude) {
                uint32_t mixed = (uint32_t)(v * UINT32_C(0x9E3779B1)) ^
                                  cfg->dither_seed;
                mixed = mix32(mixed);
                value += (mixed & 1) ? cfg->dither_amplitude :
                                      -cfg->dither_amplitude;
            }
            s->prior[v] = value;
            s->posterior[v] = value;
            if (cfg->has_memory_q) {
                uint64_t mixed = ((uint64_t)v * UINT64_C(0x9E3779B97F4A7C15)) ^
                                 cfg->memory_seed;
                mixed = mix64(mixed);
                uint64_t span = (uint64_t)(cfg->memory_high_q - cfg->memory_low_q);
                int32_t q = cfg->memory_low_q +
                    (int32_t)(((mixed >> 32) * span) >> 32);
                s->memory_q[v] = (int16_t)q;
            }
        }
    }
    memset(s->messages, 0, sizeof(*s->messages) * im->edges);
    // Incremental syndrome tracking starts from the all-zero correction.
    // Scratch is resident across requests, so these arrays must be reset.
    memset(s->correction, 0, sizeof(*s->correction) * im->variables);
    memset(s->syndrome, 0, sizeof(*s->syndrome) * im->checks);
    memset(s->residual, 0, sizeof(*s->residual) * im->checks);
}

static inline void toggle_fault_syndrome(const Image *im, uint8_t *syndrome,
                                          uint32_t fault) {
    for (uint32_t e = im->fault_offsets[fault];
         e < im->fault_offsets[fault + 1]; ++e)
        syndrome[im->fault_checks[e]] ^= 1;
}

static inline void toggle_correction_fault(const Image *im, Scratch *s,
                                           uint32_t fault) {
    s->correction[fault] ^= 1;
    toggle_fault_syndrome(im, s->syndrome, fault);
}

static void update_correction_from_posterior(const Image *im, Scratch *s) {
    for (uint32_t v = 0; v < im->variables; ++v) {
        uint8_t next = (uint8_t)(s->posterior[v] < 0);
        if (next != s->correction[v]) {
            s->correction[v] = next;
            toggle_fault_syndrome(im, s->syndrome, v);
        }
    }
}

static uint16_t logical_word(const Image *im, const uint8_t *correction) {
    uint16_t word = 0;
    for (uint32_t v = 0; v < im->variables; ++v) {
        if (correction[v]) {
            uint32_t orbit = v / GROUP_ORDER;
            uint32_t coord = v % GROUP_ORDER;
            uint16_t tid = im->orbit_label_template_ids[orbit];
            uint8_t label = im->label_templates[tid * GROUP_ORDER + coord];
            word ^= im->logical_dictionary[label];
        }
    }
    return word;
}

static int32_t correction_weight(const Image *im, const uint8_t *correction) {
    int32_t weight = 0;
    for (uint32_t v = 0; v < im->variables; ++v) weight += correction[v] != 0;
    return weight;
}

static int64_t correction_cost(const Image *im, const Scratch *s) {
    int64_t cost = 0;
    for (uint32_t v = 0; v < im->variables; ++v)
        // Portfolio selector uses immutable base priors. Dither/memory
        // perturbations steer convergence only; they must not bias coset
        // selection versus Python reference.
        cost += (int64_t)im->orbit_priors[v / GROUP_ORDER] *
                (s->correction[v] != 0);
    return cost;
}

static int tree_completion(const Image *im, const uint8_t *residual,
                           Scratch *s, uint32_t *count, uint32_t base) {
    memcpy(s->state, residual, im->checks);
    s->state[im->checks] = 0;
    *count = 0;
    for (uint32_t oi = im->order_count; oi-- > 0;) {
        uint32_t node = im->completion_order[oi];
        int32_t parent = im->completion_parents[node];
        if (s->state[node]) {
            if (parent < 0) {
                if (node != im->checks) return 0;
            } else {
                int32_t fault = im->completion_faults[node];
                if (fault < 0 || (uint32_t)fault >= im->variables) return 0;
                s->flips[base + (*count)++] = fault;
                s->state[parent] ^= 1;
            }
        }
    }
    return 1;
}

static int bridge_completion(const Image *im, const uint8_t *residual,
                             Scratch *s, uint32_t *count) {
    uint64_t parity_lo = 0, parity_hi = 0;
    for (uint32_t c = 0; c < im->checks; ++c) {
        if (!residual[c]) continue;
        int32_t component = im->component_by_check[c];
        if (component < 0) continue;
        if (component < 64) parity_lo ^= UINT64_C(1) << component;
        else parity_hi ^= UINT64_C(1) << (component - 64);
    }
    uint64_t selected_lo = 0, selected_hi = 0;
    uint32_t bridge_count = 0;
    for (uint32_t i = 0; i < im->bridge_rank; ++i) {
        int pivot;
        if (im->bridge_vectors_hi[i])
            pivot = 64 + (63 - __builtin_clzll(im->bridge_vectors_hi[i]));
        else if (im->bridge_vectors_lo[i])
            pivot = 63 - __builtin_clzll(im->bridge_vectors_lo[i]);
        else continue;
        int set = (pivot < 64) ? ((parity_lo >> pivot) & 1) :
                                 ((parity_hi >> (pivot - 64)) & 1);
        if (set) {
            parity_lo ^= im->bridge_vectors_lo[i];
            parity_hi ^= im->bridge_vectors_hi[i];
            selected_lo ^= im->bridge_combinations_lo[i];
            selected_hi ^= im->bridge_combinations_hi[i];
        }
    }
    if (parity_lo || parity_hi) return 0;
    memcpy(s->residual, residual, im->checks);
    *count = 0;
    for (uint32_t i = 0; i < im->bridge_rank; ++i) {
        int selected = (i < 64) ? ((selected_lo >> i) & 1) :
                                  ((selected_hi >> (i - 64)) & 1);
        if (!selected) continue;
        uint32_t fault = im->bridge_faults[i];
        s->flips[bridge_count++] = (int32_t)fault;
    }
    // Apply selected bridge columns through prebuilt fault incidence.
    for (uint32_t i = 0; i < bridge_count; ++i) {
        uint32_t fault = (uint32_t)s->flips[i];
        for (uint32_t e = im->fault_offsets[fault];
             e < im->fault_offsets[fault + 1]; ++e)
            s->residual[im->fault_checks[e]] ^= 1;
    }
    uint32_t tree_count = 0;
    if (!tree_completion(im, s->residual, s, &tree_count, bridge_count)) return 0;
    *count = bridge_count + tree_count;
    return 1;
}

static int apply_completion(const Image *im, Scratch *s, const uint8_t *target,
                            int use_bridge) {
    uint32_t count = 0;
    int ok = use_bridge ? bridge_completion(im, s->residual, s, &count) :
                         tree_completion(im, s->residual, s, &count, 0);
    if (!ok) return 0;
    for (uint32_t i = 0; i < count; ++i)
        toggle_correction_fault(im, s, (uint32_t)s->flips[i]);
    if (memcmp(s->syndrome, target, im->checks) == 0) return 1;
    for (uint32_t i = 0; i < count; ++i)
        toggle_correction_fault(im, s, (uint32_t)s->flips[i]);
    return 0;
}

static int32_t relay_gamma_q(uint32_t seed, uint32_t set, uint32_t variable) {
    // Same splitmix64 gamma stream as _explicit_hash_gammas() in Python and
    // native Relay audit: gamma uniformly spans [-0.24, 0.66], then truncates
    // toward zero at Q7 conversion.
    uint64_t x = (uint64_t)seed +
        (uint64_t)set * UINT64_C(0x9E3779B97F4A7C15) +
        (uint64_t)variable * UINT64_C(0xD1B54A32D192ED03);
    x ^= x >> 30; x *= UINT64_C(0xBF58476D1CE4E5B9);
    x ^= x >> 27; x *= UINT64_C(0x94D049BB133111EB);
    x ^= x >> 31;
    __int128 numerator = -((__int128)3072 << 53) +
        (__int128)(x >> 11) * 11520;
    __int128 denominator = (__int128)100 << 53;
    return (int32_t)(numerator >= 0 ? numerator / denominator :
                     -((-numerator) / denominator));
}

// Gamma values are deterministic for a resident worker.  The old hot loop
// regenerated the splitmix stream and performed a signed __int128 division
// for every variable on every Relay-lite iteration.  Cache the quantized Q7
// table once per seed instead: this changes no values or ordering, while
// removing millions of wide divisions from each rare handoff.
static int16_t *build_relay_gamma_table(const Image *im, uint32_t seed) {
    size_t rows = (size_t)RELAY_LITE_FALLBACK_SETS + 1;
    size_t count = rows * im->variables;
    int16_t *table = calloc(count, sizeof(*table));
    if (!table) return NULL;
    for (uint32_t set = 1; set <= RELAY_LITE_FALLBACK_SETS; ++set) {
        int16_t *row = table + (size_t)set * im->variables;
        for (uint32_t variable = 0; variable < im->variables; ++variable)
            row[variable] = (int16_t)relay_gamma_q(seed, set, variable);
    }
    return table;
}

static void init_relay_lite_state(const Image *im, Scratch *s) {
    for (uint32_t orbit = 0; orbit < im->orbits; ++orbit) {
        int32_t p = (int32_t)im->orbit_priors[orbit] * 32;
        uint32_t base = orbit * GROUP_ORDER;
        for (uint32_t c = 0; c < GROUP_ORDER; ++c) {
            s->prior[base + c] = (int16_t)p;
            s->prior_div128[base + c] = trunc_div128_lookup(p);
            s->posterior[base + c] = (int16_t)p;
        }
    }
    memset(s->messages, 0, sizeof(*s->messages) * im->edges);
    memset(s->correction, 0, sizeof(*s->correction) * im->variables);
    memset(s->syndrome, 0, sizeof(*s->syndrome) * im->checks);
    memset(s->residual, 0, sizeof(*s->residual) * im->checks);
}

static Result run_relay_lite_range(const Image *im, const uint8_t *target,
                                   Scratch *s, const int16_t *gamma_table,
                                   uint32_t first_set, uint32_t end_set,
                                   int initialize, const Result *seed_result) {
    Result best = {
        .accepted = 0, .syndrome_exact = 0, .logical = 0, .weight = 0,
        .prior_cost = 0, .iterations = 0, .sets_attempted = 0,
        .stage_reached = 0,
        .reason = "C Relay-lite did not produce exact candidate",
    };
    int have_best = seed_result && seed_result->accepted;
    uint32_t converged = 0;
    uint32_t total_iterations = seed_result ? seed_result->iterations : 0;
    uint32_t sets_attempted = seed_result ? seed_result->sets_attempted : 0;
    uint32_t stage_reached = seed_result ? seed_result->stage_reached : 0;
    if (seed_result && seed_result->accepted) best = *seed_result;
    if (initialize) {
        init_relay_lite_state(im, s);
        total_iterations = 0;
        have_best = 0;
    }
    for (uint32_t set = first_set; set < end_set; ++set) {
        ++sets_attempted;
        stage_reached = set >= RELAY_LITE_SETS ? 2 : 1;
        int32_t memory_q0 = set == 0 ? 12 : 0;
        memset(s->messages, 0, sizeof(*s->messages) * im->edges);
        for (uint32_t iteration = 1;
             iteration <= (set == 0 ? g_relay_lite_pre : g_relay_lite_set_iters);
             ++iteration) {
            int use_prior_v2c = set != 0 && iteration == 1;
            // Check nodes are independent within a layered iteration. Keep
            // each check's edge order unchanged, but expose the whole pass
            // to OpenMP; the old serial pass dominated the resident tail.
#pragma omp parallel for schedule(static)
            for (uint32_t c = 0; c < im->checks; ++c) {
                uint16_t first = UINT16_MAX, second = UINT16_MAX, count = 0;
                uint8_t parity = 0;
                for (uint32_t e = im->offsets[c]; e < im->offsets[c + 1]; ++e) {
                    uint32_t v = im->edge_faults[e];
                    int32_t x = use_prior_v2c ? s->prior[v] :
                        (int32_t)s->posterior[v] - s->messages[e];
                    uint16_t mag = (uint16_t)(x < 0 ? -x : x);
                    parity ^= (x < 0);
                    if (mag < first) {
                        second = first; first = mag; count = 1;
                    } else if (mag == first) {
                        ++count;
                    } else if (mag < second) {
                        second = mag;
                    }
                }
                if (im->offsets[c + 1] - im->offsets[c] == 1) second = 0;
                s->first[c] = first; s->second[c] = second;
                s->first_count[c] = count; s->parity[c] = parity;
            }
            // Each check owns a disjoint CSR edge range, so message writes
            // are race-free and preserve the exact update equations.
#pragma omp parallel for schedule(static)
            for (uint32_t c = 0; c < im->checks; ++c) {
                uint16_t first = s->first[c], second = s->second[c];
                uint16_t count = s->first_count[c];
                for (uint32_t e = im->offsets[c]; e < im->offsets[c + 1]; ++e) {
                    uint32_t v = im->edge_faults[e];
                    int32_t x = use_prior_v2c ? s->prior[v] :
                        (int32_t)s->posterior[v] - s->messages[e];
                    uint16_t mag = (uint16_t)(x < 0 ? -x : x);
                    int32_t outgoing = (mag == first && count == 1) ?
                        second : first;
                    int negative = target[c] ^ s->parity[c] ^ (x < 0);
                    int32_t updated = negative ? -outgoing : outgoing;
                    if (updated > 2047) updated = 2047;
                    if (updated < -2047) updated = -2047;
                    s->messages[e] = (int16_t)updated;
                }
            }
            // Gather through immutable variable->edge CSR. The CSR was
            // emitted in check/edge order, so integer accumulation is
            // unchanged while random scatter writes disappear.
#pragma omp parallel for schedule(static)
            for (uint32_t v = 0; v < im->variables; ++v) {
                int32_t sum = 0;
                for (uint32_t slot = im->fault_offsets[v];
                     slot < im->fault_offsets[v + 1]; ++slot)
                    sum += s->messages[im->fault_edges[slot]];
                s->summed[v] = sum;
                int32_t q = memory_q0;
                if (set != 0)
                    q = gamma_table[(size_t)set * im->variables + v];
                int64_t memory =
                    (int64_t)s->prior_div128[v] * (128 - q) +
                    (int64_t)trunc_div128_lookup(s->posterior[v]) * q;
                int64_t value = memory + s->summed[v];
                if (value < -2047) value = -2047;
                if (value > 2047) value = 2047;
                s->posterior[v] = (int16_t)value;
            }
            update_correction_from_posterior(im, s);
            ++total_iterations;
            if (memcmp(s->syndrome, target, im->checks) == 0) {
                Result current = {
                    .accepted = 1, .syndrome_exact = 1,
                    .logical = logical_word(im, s->correction),
                    .weight = correction_weight(im, s->correction),
                    .prior_cost = correction_cost(im, s),
                    .iterations = total_iterations,
                    .sets_attempted = sets_attempted,
                    .stage_reached = stage_reached,
                    .reason = "C Relay-lite exact",
                };
                ++converged;
                if (!have_best || current.prior_cost < best.prior_cost ||
                    (current.prior_cost == best.prior_cost &&
                     current.weight < best.weight)) {
                    best = current; have_best = 1;
                }
                break;
            }
        }
        if (converged >= g_relay_lite_stop) break;
    }
    if (!have_best) {
        best.logical = logical_word(im, s->correction);
        best.weight = correction_weight(im, s->correction);
        best.prior_cost = correction_cost(im, s);
        best.iterations = total_iterations;
    }
    best.sets_attempted = sets_attempted;
    best.stage_reached = stage_reached;
    return best;
}

static int relay_needs_fallback(const Result *result,
                                const Result *portfolio_best,
                                int portfolio_have) {
    if (!result->accepted || strcmp(result->reason, "C Relay-lite exact") != 0)
        return 1;
    return portfolio_have && result->logical != portfolio_best->logical;
}

static Result run_relay_lite_adaptive(const Image *im, const uint8_t *target,
                                      Scratch *s, const int16_t *gamma_table,
                                      const Result *portfolio_best,
                                      int portfolio_have) {
    uint32_t primary_end = g_relay_lite_sets < g_relay_lite_fallback_sets ?
                           g_relay_lite_sets : g_relay_lite_fallback_sets;
    Result result = run_relay_lite_range(
        im, target, s, gamma_table, 0, primary_end, 1, NULL);
    if (g_relay_lite_fallback_sets > primary_end &&
        relay_needs_fallback(&result, portfolio_best, portfolio_have)) {
        // Continue from the first unvisited set. The previous implementation
        // restarted at set 0, so its nominal 32->240 escalation repeated the
        // primary work and could terminate before exploring new sets.
        result = run_relay_lite_range(
            im, target, s, gamma_table, primary_end,
            g_relay_lite_fallback_sets, 0, &result);
    }
    return result;
}

static Result run_config(const Image *im, const uint8_t *target,
                         const Config *cfg, Scratch *s) {
    Result result = {
        .accepted = 0, .syndrome_exact = 0, .logical = 0, .weight = 0,
        .prior_cost = 0, .iterations = 0, .sets_attempted = 0,
        .stage_reached = 0,
        .reason = "fixed-point streamed S2R Relay tail did not satisfy syndrome",
    };
    const int32_t message_max = (1 << cfg->message_bits) - 1;
    const int32_t divisor = 1 << cfg->memory_weight_shift;
    init_state(im, cfg, s);
    for (uint32_t iteration = 1; iteration <= cfg->max_iterations; ++iteration) {
        // Relay check-node passes have the same independent ownership as the
        // fixed-point portfolio above. Parallelize without changing ordering
        // inside any check or its edge range.
#pragma omp parallel for schedule(static)
        for (uint32_t c = 0; c < im->checks; ++c) {
            uint16_t first = UINT16_MAX, second = UINT16_MAX, count = 0;
            uint8_t parity = 0;
            for (uint32_t e = im->offsets[c]; e < im->offsets[c + 1]; ++e) {
                uint32_t v = im->edge_faults[e];
                int32_t x = (int32_t)s->posterior[v] - s->messages[e];
                uint16_t mag = (uint16_t)(x < 0 ? -x : x);
                parity ^= (x < 0);
                if (mag < first) { second = first; first = mag; count = 1; }
                else if (mag == first) { ++count; }
                else if (mag < second) second = mag;
            }
            if (im->offsets[c + 1] - im->offsets[c] == 1) second = 0;
            s->first[c] = first; s->second[c] = second;
            s->first_count[c] = count; s->parity[c] = parity;
        }
        // Message writes remain disjoint because each edge belongs to one
        // check row in the immutable CSR image.
#pragma omp parallel for schedule(static)
        for (uint32_t c = 0; c < im->checks; ++c) {
            uint16_t first = s->first[c], second = s->second[c];
            uint16_t count = s->first_count[c];
            for (uint32_t e = im->offsets[c]; e < im->offsets[c + 1]; ++e) {
                uint32_t v = im->edge_faults[e];
                int32_t x = (int32_t)s->posterior[v] - s->messages[e];
                uint16_t mag = (uint16_t)(x < 0 ? -x : x);
                int32_t outgoing = (mag == first && count == 1) ? second : first;
                if (outgoing > message_max) outgoing = message_max;
                int negative = target[c] ^ s->parity[c] ^ (x < 0);
                int32_t proposed = negative ? -outgoing : outgoing;
                int32_t combined = (int32_t)s->messages[e] + proposed;
                int32_t updated = combined >= 0 ? (combined + 1) / 2 :
                    -((-combined + 1) / 2);
                if (updated > message_max) updated = message_max;
                if (updated < -message_max) updated = -message_max;
                s->messages[e] = (int16_t)updated;
            }
        }
        #pragma omp parallel for schedule(static)
        for (uint32_t v = 0; v < im->variables; ++v) {
            int32_t sum = 0;
            for (uint32_t slot = im->fault_offsets[v];
                 slot < im->fault_offsets[v + 1]; ++slot)
                sum += s->messages[im->fault_edges[slot]];
            s->summed[v] = sum;
            int64_t memory;
            if (cfg->has_memory_q) {
                int64_t q = s->memory_q[v];
                memory = floor_div_i64((128 - q) * s->prior[v] +
                                       q * s->posterior[v], 128);
            } else {
                memory = floor_div_i64((divisor - 1) * s->prior[v] +
                                       s->posterior[v], divisor);
            }
            int64_t value = memory + s->summed[v];
            if (value < -1024) value = -1024;
            if (value > 1023) value = 1023;
            s->posterior[v] = (int16_t)value;
        }
        update_correction_from_posterior(im, s);
        if (memcmp(s->syndrome, target, im->checks) == 0) {
            result.accepted = result.syndrome_exact = 1;
            result.logical = logical_word(im, s->correction);
            result.weight = correction_weight(im, s->correction);
            result.prior_cost = correction_cost(im, s);
            result.iterations = iteration;
            result.reason = "";
            return result;
        }
        for (uint32_t c = 0; c < im->checks; ++c)
            s->residual[c] = s->syndrome[c] ^ target[c];
        // Match Python contract: ordinary tree completion each iteration;
        // component bridge after terminal fixed point.
        if (apply_completion(im, s, target, 0)) {
            result.accepted = result.syndrome_exact = 1;
            result.logical = logical_word(im, s->correction);
            result.weight = correction_weight(im, s->correction);
            result.prior_cost = correction_cost(im, s);
            result.iterations = iteration;
            result.reason = "early logical-neutral degree-1/2 completion";
            return result;
        }
    }
    for (uint32_t c = 0; c < im->checks; ++c)
        s->residual[c] = s->syndrome[c] ^ target[c];
    if (apply_completion(im, s, target, 1)) {
        result.accepted = result.syndrome_exact = 1;
        result.logical = logical_word(im, s->correction);
        result.weight = correction_weight(im, s->correction);
        result.prior_cost = correction_cost(im, s);
        result.iterations = cfg->max_iterations;
        result.reason = "logical-neutral bridged degree-1/2 completion";
        return result;
    }
    result.logical = logical_word(im, s->correction);
    result.weight = correction_weight(im, s->correction);
    result.prior_cost = correction_cost(im, s);
    result.iterations = cfg->max_iterations;
    return result;
}

static void configs_for_basis(int is_z, Config cfg[MAX_CONFIGS]) {
    uint32_t short_max = is_z ? 28 : 20;
    uint32_t medium_max = is_z ? 32 : 28;
    cfg[0] = (Config){short_max, 4, 6, 0, 0, 0, 0, 0, 0};
    cfg[1] = (Config){short_max, 2, 6, 0, 0, 0, 0, 0, 0};
    cfg[2] = (Config){medium_max, 4, 7, 0, 0, -31, 84, 4, 1};
    cfg[3] = (Config){medium_max, 4, 7, 0, 0, -31, 84, 2, 1};
    cfg[4] = (Config){medium_max, 4, 7, 1, 43, 0, 0, 0, 0};
    cfg[5] = (Config){is_z ? 36 : 32, 4, 7, 2, 2, 0, 0, 0, 0};
    cfg[6] = (Config){is_z ? 44 : 40, 4, 7, 0, 0, -64, 128, 2, 1};
    // Relay-style escape legs found by targeted wrong-coset analysis.
    cfg[7] = (Config){is_z ? 36 : 32, 4, 8, 4, 43091, 0, 0, 0, 0};
    cfg[8] = (Config){is_z ? 36 : 32, 4, 8, 0, 0, -64, 128, 43091, 1};
}

static int parse_hex_request(const char *line, uint8_t *target, uint32_t checks) {
    const char *p = strstr(line, "\"syndrome_hex\"");
    if (!p) return 0;
    p = strchr(p, ':'); if (!p) return 0;
    while (*++p == ' ' || *p == '\t') {}
    if (*p == '"') ++p;
    uint32_t bytes = (checks + 7) / 8;
    for (uint32_t i = 0; i < bytes; ++i) {
        unsigned value;
        if (sscanf(p + 2 * i, "%2x", &value) != 1) return 0;
        for (uint32_t bit = 0; bit < 8 && 8 * i + bit < checks; ++bit)
            target[8 * i + bit] = (uint8_t)((value >> bit) & 1U);
    }
    return 1;
}

static int env_u32_exact(const char *name, uint32_t fallback, uint32_t minimum,
                         uint32_t maximum, uint32_t *out) {
    const char *value = getenv(name);
    if (!value || !*value) { *out = fallback; return 1; }
    errno = 0;
    char *end = NULL;
    unsigned long parsed = strtoul(value, &end, 10);
    if (end == value || *end != '\0' || errno == ERANGE ||
        parsed < minimum || parsed > maximum) {
        fprintf(stderr, "%s=%s outside accepted range [%u,%u]\n",
                name, value, minimum, maximum);
        return 0;
    }
    *out = (uint32_t)parsed;
    return 1;
}

static int env_bool(const char *name) {
    const char *value = getenv(name);
    return value && (!strcmp(value, "1") || !strcmp(value, "TRUE") ||
                     !strcmp(value, "true") || !strcmp(value, "ON") ||
                     !strcmp(value, "on"));
}

static void print_result(const Result *result, uint64_t wall_ns, int candidate_count,
                         const RequestTiming *timing) {
    printf("{\"ok\":true,\"accepted\":%s,\"predicted_logicals\":[",
           result->accepted ? "true" : "false");
    for (int i = 0; i < LOGICALS; ++i)
        printf("%s%d", i ? "," : "", (result->logical >> i) & 1);
    printf("],\"syndrome_exact\":%s,\"backend\":\"c_relay\","
           "\"stage\":\"HOST_RELAY_C\",\"iterations\":%u,"
           "\"candidate_count\":%d,\"wall_ns\":%llu,\"reason\":\"%s\","
           "\"portfolio_ns\":%llu,\"selection_ns\":%llu,"
           "\"relay_primary_ns\":%llu,\"relay_escape_ns\":%llu,"
           "\"relay_runs\":%u,\"portfolio_configs\":%u,"
           "\"relay_primary_iterations\":%u,"
           "\"relay_escape_iterations\":%u,"
           "\"relay_primary_sets\":%u,\"relay_escape_sets\":%u,"
           "\"relay_primary_stage\":%u,\"relay_escape_stage\":%u,"
           "\"relay_sets_attempted\":%u,\"relay_stage_reached\":%u,"
           "\"logical_disagreement\":%s}\n",
           result->syndrome_exact ? "true" : "false", result->iterations,
           candidate_count, (unsigned long long)wall_ns, result->reason,
           (unsigned long long)timing->portfolio_ns,
           (unsigned long long)timing->selection_ns,
           (unsigned long long)timing->relay_primary_ns,
           (unsigned long long)timing->relay_escape_ns,
           timing->relay_runs, timing->portfolio_configs,
           timing->relay_primary_iterations,
           timing->relay_escape_iterations,
           timing->relay_primary_sets, timing->relay_escape_sets,
           timing->relay_primary_stage, timing->relay_escape_stage,
           timing->relay_primary_sets + timing->relay_escape_sets,
           timing->relay_primary_stage > timing->relay_escape_stage ?
               timing->relay_primary_stage : timing->relay_escape_stage,
           timing->logical_disagreement ? "true" : "false");
    fflush(stdout);
}

int main(int argc, char **argv) {
    if (argc < 3 || argc > 4) {
        fprintf(stderr, "usage: c_tail_worker IMAGE BASIS(X|Z) [config-index]\n");
        return 2;
    }
    int only_config = -1;
    if (argc == 4) {
        only_config = atoi(argv[3]);
        if (only_config < 0 || only_config >= MAX_CONFIGS) return 2;
    }
    init_trunc_div128_table();
    Image image;
    if (!load_image(argv[1], &image)) {
        fprintf(stderr, "failed to load C tail image: %s\n", argv[1]);
        return 3;
    }
    int is_z = (argv[2][0] == 'Z' || argv[2][0] == 'z');
    Scratch scratches[MAX_CONFIGS];
    memset(scratches, 0, sizeof(scratches));
    for (int i = 0; i < MAX_CONFIGS; ++i) {
        if (!alloc_scratch(&image, &scratches[i])) {
            for (int j = 0; j <= i; ++j) free_scratch(&scratches[j]);
            free_image(&image); return 4;
        }
    }
    uint8_t *target = calloc(image.checks, 1);
    if (!target) {
        for (int i = 0; i < MAX_CONFIGS; ++i) free_scratch(&scratches[i]);
        free_image(&image); return 5;
    }
    Config configs[MAX_CONFIGS];
    configs_for_basis(is_z, configs);
    if (!env_u32_exact("GROSS144_C_TAIL_RELAY_SETS", RELAY_LITE_SETS,
                       1, RELAY_SET_COUNT_MAX, &g_relay_lite_sets) ||
        !env_u32_exact("GROSS144_C_TAIL_RELAY_FALLBACK_SETS",
                       RELAY_LITE_FALLBACK_SETS, 1, RELAY_SET_COUNT_MAX,
                       &g_relay_lite_fallback_sets) ||
        !env_u32_exact("GROSS144_C_TAIL_RELAY_PRE", RELAY_LITE_PRE,
                       0, RELAY_PRE_MAX, &g_relay_lite_pre) ||
        !env_u32_exact("GROSS144_C_TAIL_RELAY_SET_ITERS", RELAY_LITE_SET_ITERS,
                       1, RELAY_SET_MAX, &g_relay_lite_set_iters) ||
        !env_u32_exact("GROSS144_C_TAIL_RELAY_STOP", RELAY_LITE_STOP,
                       0, RELAY_STOP_MAX, &g_relay_lite_stop)) {
        free(target);
        for (int i = 0; i < MAX_CONFIGS; ++i) free_scratch(&scratches[i]);
        free_image(&image); return 7;
    }
    if (g_relay_lite_fallback_sets < g_relay_lite_sets) {
        fprintf(stderr, "GROSS144_C_TAIL_RELAY_FALLBACK_SETS must be >= RELAY_SETS\n");
        free(target);
        for (int i = 0; i < MAX_CONFIGS; ++i) free_scratch(&scratches[i]);
        free_image(&image); return 7;
    }
    g_skip_relay = env_bool("GROSS144_C_TAIL_SKIP_RELAY");
    g_relay_only = env_bool("GROSS144_C_TAIL_RELAY_ONLY");
    g_relay_escape_only = env_bool("GROSS144_C_TAIL_RELAY_ESCAPE_ONLY");
    int16_t *gamma_primary = build_relay_gamma_table(
        &image, RELAY_LITE_PRIMARY_SEED);
    int16_t *gamma_escape = build_relay_gamma_table(
        &image, RELAY_LITE_ESCAPE_SEED);
    if (!gamma_primary || !gamma_escape) {
        free(gamma_primary); free(gamma_escape); free(target);
        for (int i = 0; i < MAX_CONFIGS; ++i) free_scratch(&scratches[i]);
        free_image(&image); return 6;
    }
    const char *fast_first_env = getenv("GROSS144_C_TAIL_FAST_FIRST");
    const int fast_first = fast_first_env &&
        (strcmp(fast_first_env, "1") == 0 ||
         strcmp(fast_first_env, "TRUE") == 0 ||
         strcmp(fast_first_env, "true") == 0);
    int first_config = only_config >= 0 ? only_config : 0;
    uint32_t portfolio_limit_u32 = 0;
    if (!env_u32_exact("GROSS144_C_TAIL_PORTFOLIO_LIMIT",
                       DEFAULT_PORTFOLIO_LIMIT, 1, MAX_CONFIGS,
                       &portfolio_limit_u32)) {
        free(gamma_primary); free(gamma_escape); free(target);
        for (int i = 0; i < MAX_CONFIGS; ++i) free_scratch(&scratches[i]);
        free_image(&image); return 7;
    }
    int portfolio_limit = (int)portfolio_limit_u32;
    int last_config = only_config >= 0 ? only_config + 1 : portfolio_limit;
    printf("{\"ready\":true,\"backend\":\"c_relay\",\"candidates\":%d,"
           "\"relay_sets\":%u,\"relay_pre\":%u,"
           "\"relay_fallback_sets\":%u,\"relay_set_iters\":%u,"
           "\"relay_stop\":%u,\"portfolio_limit\":%d,"
           "\"fast_first\":%s}\n",
           last_config - first_config, g_relay_lite_sets, g_relay_lite_pre,
           g_relay_lite_fallback_sets, g_relay_lite_set_iters,
           g_relay_lite_stop, portfolio_limit, fast_first ? "true" : "false");
    fflush(stdout);
    char line[8192];
    while (fgets(line, sizeof(line), stdin)) {
        if (!parse_hex_request(line, target, image.checks)) {
            printf("{\"ok\":false,\"error\":\"invalid syndrome request\"}\n");
            fflush(stdout); continue;
        }
        uint64_t started = now_ns();
        RequestTiming timing = {0};
        Result results[MAX_CONFIGS];
        memset(results, 0, sizeof(results));
        int run_first = first_config;
        int run_last = last_config;
        if (g_relay_only) {
            run_first = 0;
            run_last = 0;
        }
        uint64_t portfolio_started = now_ns();
        // The bounded FPGA handoff is already a filtered tail.  In the
        // common case, primary config 0 reaches an exact fixed point without
        // bridge completion; do not spend eight more portfolio legs merely to
        // re-select an equivalent candidate.  Ambiguous/bridged cases retain
        // the complete audited portfolio below. This is intentionally opt-in
        // until the full corpus proves the logical result is unchanged.
        if (fast_first && only_config < 0) {
            results[0] = run_config(&image, target, &configs[0], &scratches[0]);
            if (results[0].accepted && strcmp(
                    results[0].reason,
                    "logical-neutral bridged degree-1/2 completion") != 0) {
                run_first = 0;
                run_last = 1;
            } else {
                run_first = 1;
                run_last = last_config;
            }
        }
        // Portfolio axes are independent. OpenMP keeps them concurrent while
        // each worker owns private hot arrays; no locks in decoder loop.
#pragma omp parallel for schedule(static)
        for (int i = run_first; i < run_last; ++i) {
            // Config 0 was evaluated above for the gated fast path.
            if (!(fast_first && only_config < 0 && i == 0))
                results[i] = run_config(&image, target, &configs[i], &scratches[i]);
        }
        timing.portfolio_ns = now_ns() - portfolio_started;
        timing.portfolio_configs = (uint32_t)(run_last - run_first);
        uint64_t selection_started = now_ns();
        Result primary = results[first_config], best = {0};
        int have_best = 0;
        int best_bridged = 1;
        for (int i = run_first; i < run_last; ++i) {
            Result current = results[i];
            int current_bridged = strcmp(
                current.reason, "logical-neutral bridged degree-1/2 completion") == 0;
            if (current.accepted && (!have_best ||
                current_bridged < best_bridged ||
                (current_bridged == best_bridged &&
                 (current.prior_cost < best.prior_cost ||
                  (current.prior_cost == best.prior_cost &&
                 current.weight < best.weight))))) {
                best = current; best_bridged = current_bridged; have_best = 1;
            }
        }
        const Result portfolio_best = best;
        const int portfolio_have = have_best;
        const int portfolio_bridged = best_bridged;
        // A non-bridged winner is normally the strongest candidate, but a
        // wrong-coset tail can still hide behind it while other portfolio
        // legs land in different exact cosets.  Treat disagreement as an
        // ambiguity signal and run the audited Relay-lite rescue.  This is
        // the missing trigger exposed by the 20k X failure: one leg selected
        // 0xD95 while the other exact legs disagreed and Relay-lite produced
        // the truth-free lower-cost 0x128 candidate.
        int diverse = 0;
        uint16_t reference = 0;
        int have_reference = 0;
        for (int i = run_first; i < run_last; ++i) {
            if (!results[i].accepted) continue;
            if (!have_reference) {
                reference = results[i].logical;
                have_reference = 1;
            } else if (results[i].logical != reference) {
                diverse = 1;
                break;
            }
        }
        timing.selection_ns = now_ns() - selection_started;
        int ran_relay = 0;
        if (!g_skip_relay && (!have_best || best_bridged || diverse)) {
            // Fast portfolio handles common tails. Only terminal bridge
            // candidates enter retained-posterior Relay-lite rescue.
            Result relay = {0}, escape = {0};
            uint64_t relay_started;
            if (diverse) {
                // Both retained-posterior legs are independent. Run them as
                // sections; this removes serial primary+escape tail latency
                // without changing either equation or quorum decision.
#pragma omp parallel sections
                {
#pragma omp section
                    {
                        uint64_t phase_started = now_ns();
                        relay = run_relay_lite_adaptive(
                            &image, target, &scratches[0],
                            g_relay_escape_only ? gamma_escape : gamma_primary,
                            &portfolio_best, portfolio_have);
                        timing.relay_primary_ns = now_ns() - phase_started;
                        timing.relay_primary_iterations = relay.iterations;
                        timing.relay_primary_sets = relay.sets_attempted;
                        timing.relay_primary_stage = relay.stage_reached;
                    }
#pragma omp section
                    {
                        uint64_t phase_started = now_ns();
                        escape = run_relay_lite_adaptive(
                            &image, target, &scratches[1], gamma_escape,
                            &portfolio_best, portfolio_have);
                        timing.relay_escape_ns = now_ns() - phase_started;
                        timing.relay_escape_iterations = escape.iterations;
                        timing.relay_escape_sets = escape.sets_attempted;
                        timing.relay_escape_stage = escape.stage_reached;
                    }
                }
                ran_relay = 2;
            } else {
                relay_started = now_ns();
                relay = run_relay_lite_adaptive(
                    &image, target, &scratches[0],
                    g_relay_escape_only ? gamma_escape : gamma_primary,
                    &portfolio_best, portfolio_have);
                timing.relay_primary_ns = now_ns() - relay_started;
                timing.relay_primary_iterations = relay.iterations;
                timing.relay_primary_sets = relay.sets_attempted;
                timing.relay_primary_stage = relay.stage_reached;
                ran_relay = 1;
            }
            // A fixed gamma stream can repeatedly choose one wrong logical
            // coset.  Only spend the second Relay-lite leg when the fast
            // images disagree; common exact tails keep one leg of work.
            if (diverse) {
                if (relay.accepted && escape.accepted) {
                    // Three-way quorum: one fast rescue leg may not replace
                    // two agreeing independent candidates. Two agreeing
                    // rescue legs may replace the portfolio winner, which is
                    // the known X wrong-coset case.
                    if (relay.logical == escape.logical) {
                        best = relay; have_best = 1; best_bridged = 0;
                    } else if (portfolio_have &&
                               escape.logical == portfolio_best.logical) {
                        best = portfolio_best;
                        have_best = 1;
                        best_bridged = portfolio_bridged;
                    } else if (portfolio_have &&
                               relay.logical == portfolio_best.logical) {
                        best = portfolio_best;
                        have_best = 1;
                        best_bridged = portfolio_bridged;
                    } else {
                        // No quorum: fall back to the cost selector over all
                        // exact candidates rather than trusting one stream.
                        best = portfolio_best;
                        have_best = portfolio_have;
                        best_bridged = portfolio_bridged;
                        if (relay.accepted &&
                            (!have_best || relay.prior_cost < best.prior_cost ||
                             (relay.prior_cost == best.prior_cost &&
                              relay.weight < best.weight))) {
                            best = relay; have_best = 1; best_bridged = 0;
                        }
                        if (escape.accepted &&
                            (!have_best || escape.prior_cost < best.prior_cost ||
                             (escape.prior_cost == best.prior_cost &&
                              escape.weight < best.weight))) {
                            best = escape; have_best = 1; best_bridged = 0;
                        }
                    }
                } else {
                    // Preserve cost-based selection if one rescue leg did not
                    // reach an exact syndrome.
                    best = portfolio_best;
                    have_best = portfolio_have;
                    best_bridged = portfolio_bridged;
                    if (relay.accepted &&
                        (!have_best || relay.prior_cost < best.prior_cost ||
                         (relay.prior_cost == best.prior_cost &&
                          relay.weight < best.weight))) {
                        best = relay; have_best = 1; best_bridged = 0;
                    }
                    if (escape.accepted &&
                        (!have_best || escape.prior_cost < best.prior_cost ||
                         (escape.prior_cost == best.prior_cost &&
                          escape.weight < best.weight))) {
                        best = escape; have_best = 1; best_bridged = 0;
                    }
                }
            } else if (relay.accepted &&
                       (!have_best || relay.prior_cost < best.prior_cost ||
                        (relay.prior_cost == best.prior_cost &&
                         relay.weight < best.weight) ||
                        (best_bridged && relay.syndrome_exact))) {
                best = relay; have_best = 1; best_bridged = 0;
            }
        }
        if (!have_best) best = primary;
        timing.relay_runs = (uint32_t)ran_relay;
        timing.logical_disagreement = diverse;
        print_result(&best, now_ns() - started,
                     run_last - run_first + ran_relay, &timing);
    }
    free(target);
    free(gamma_primary);
    free(gamma_escape);
    for (int i = 0; i < MAX_CONFIGS; ++i) free_scratch(&scratches[i]);
    free_image(&image);
    return 0;
}
