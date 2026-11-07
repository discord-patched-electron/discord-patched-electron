// SPDX-License-Identifier: GPL-3.0-only
#ifndef DPE_KMS_GPU_H
#define DPE_KMS_GPU_H
#include "kms-client.h"
#include "vkcapture-wire.h"
struct dpe_kms_gpu;
struct dpe_kms_buffer;
struct dpe_kms_gpu *dpe_kms_gpu_create(const char *);
void dpe_kms_gpu_destroy(struct dpe_kms_gpu *);
int dpe_kms_gpu_fd(struct dpe_kms_gpu *);
struct dpe_kms_buffer *dpe_kms_buffer_create(struct dpe_kms_gpu *, uint32_t, uint32_t,
                                             struct dpe_vk_packet *, int[4]);
void dpe_kms_buffer_destroy(struct dpe_kms_buffer *);
// Returns an owned completion sync_file. Input is imported through EGL's
// implicit DMA-BUF synchronization; output is published with explicit sync.
int dpe_kms_buffer_render(struct dpe_kms_buffer *, const struct dpe_kms_scene *);
// One-shot picker thumbnail. Downscale on the GPU, then read only the bounded
// thumbnail into BGRA bytes. Never used by the streaming/encoder path.
bool dpe_kms_gpu_preview(struct dpe_kms_gpu *, const struct dpe_kms_scene *, uint32_t, uint32_t,
                         uint32_t *, uint32_t *, unsigned char *);
#endif
