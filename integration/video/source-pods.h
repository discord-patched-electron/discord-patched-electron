// SPDX-License-Identifier: MIT
#ifndef DPE_SOURCE_PODS_H
#define DPE_SOURCE_PODS_H
#include <stdint.h>
struct spa_pod;
typedef int (*dpe_source_apply)(void *, const struct spa_pod **, uint32_t);
// Params are aligned, immutable and borrowed only until the callback returns.
int dpe_source_format(uint32_t width, uint32_t height, uint32_t format, uint64_t modifier,
                      uint32_t fps, void *data, dpe_source_apply apply);
int dpe_source_buffers(uint32_t width, uint32_t height, uint32_t relay_planes, void *data,
                       dpe_source_apply apply);
#endif
