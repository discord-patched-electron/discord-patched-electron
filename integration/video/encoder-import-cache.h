// SPDX-License-Identifier: MIT
// Opaque Rust import leases. No C caller reads or owns cache implementation state.
#ifndef DPE_ENCODER_IMPORT_CACHE_H
#define DPE_ENCODER_IMPORT_CACHE_H
#include "encoder-abi.h"
#include <stdbool.h>
#include <stdint.h>
#define DPE_ENCODER_IMPORTS 4
struct dpe_import_cache;
struct dpe_import_entry;
// Context transfers only on success. Callbacks may run on any frame-release
// thread and are serialized per device; they must not reenter this cache.
// finish follows the last GPU destruction and descriptor release.
struct dpe_import_cache *
dpe_import_cache_new(void *, bool (*)(void *, const struct encoder_nv12 *, uintptr_t *),
                     void (*)(void *, uintptr_t), void (*)(void *));
struct dpe_import_entry *dpe_import_get(struct dpe_import_cache *, const struct encoder_nv12 *);
// A successful get owns one distinct lease, even for an already imported surface.
bool dpe_import_surface(struct dpe_import_entry *, uintptr_t *);
void dpe_import_put(struct dpe_import_entry *);
void dpe_import_cache_close(struct dpe_import_cache *);
void dpe_import_stats(uint64_t[4]);
#endif
