// SPDX-License-Identifier: MIT
#ifndef DPE_DIRECT_CAPTURE_H
#define DPE_DIRECT_CAPTURE_H
#include <stdbool.h>
#include <stdint.h>

bool dpe_direct_capture_ready(void);
bool dpe_direct_capture_in_delivery(void);
int32_t dpe_direct_capture_new(const void *, void **);
bool dpe_direct_capture_update(void *, const void *, int32_t *);
bool dpe_direct_capture_destroy(void *, int32_t *);
bool dpe_direct_capture_renditions(void *);
void dpe_direct_capture_stats(uint64_t[5]);
int dpe_capture_picker_linkage(uintptr_t, uintptr_t, uintptr_t);
#endif
