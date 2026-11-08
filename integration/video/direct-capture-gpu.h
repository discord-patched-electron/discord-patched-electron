// SPDX-License-Identifier: MIT
#ifndef DPE_DIRECT_CAPTURE_GPU_H
#define DPE_DIRECT_CAPTURE_GPU_H
#include "direct-capture-wire.h"
#include "encoder-abi.h"

struct dpe_direct_gpu;
struct dpe_direct_gpu *dpe_direct_gpu_create(const char *node);
void dpe_direct_gpu_destroy(struct dpe_direct_gpu *);
// Input descriptors are borrowed only during conversion. Completion is checked
// before returning, so the caller can acknowledge/release the producer lease.
void *dpe_direct_gpu_convert(struct dpe_direct_gpu *, const struct dpe_direct_packet *,
                             const int[DPE_DIRECT_FDS], unsigned width, unsigned height);
bool dpe_direct_frame_owned(const void *);
bool dpe_direct_frame_destroy(void *);
bool dpe_direct_frame_data(const void *, size_t *, size_t *, uint64_t *);
bool dpe_direct_frame_view(void *, struct encoder_nv12 *, void **, const char **);
void dpe_direct_frame_release(void *);
void *dpe_direct_frame_i420(void *, const uint8_t **, size_t *, size_t *, size_t *, size_t *,
                            size_t *);
bool dpe_direct_i420_destroy(void *);
bool dpe_direct_frame_cpu_nv12(void *, const uint8_t **, size_t *, size_t *, size_t *);
uint64_t dpe_direct_gpu_readbacks(void);
#endif
