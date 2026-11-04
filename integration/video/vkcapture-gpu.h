// SPDX-License-Identifier: MIT
#ifndef DPE_VKCAPTURE_GPU_H
#define DPE_VKCAPTURE_GPU_H
#include "vkcapture-wire.h"
#include <pipewire/pipewire.h>
struct dpe_vk_gpu;
struct dpe_vk_buffer;
struct dpe_vk_gpu *dpe_vk_gpu_create(const char *, struct pw_loop *, uint32_t, uint32_t);
void dpe_vk_gpu_destroy(struct dpe_vk_gpu *);
const uint8_t *dpe_vk_gpu_uuid(struct dpe_vk_gpu *);
int dpe_vk_gpu_fd(struct dpe_vk_gpu *);
int dpe_vk_gpu_import(struct dpe_vk_gpu *, const struct dpe_vk_packet *, const int[4]);
// Picker-only readback: GPU scales first, never maps the full-resolution source.
// Caller owns at least requested_width*requested_height*4 bytes, capped 480x270.
int dpe_vk_gpu_preview(struct dpe_vk_gpu *, uint32_t *, uint32_t *, uint8_t *);
struct dpe_vk_buffer *dpe_vk_buffer_create(struct dpe_vk_gpu *, struct spa_data *, uint32_t,
                                           uint32_t);
void dpe_vk_buffer_destroy(struct dpe_vk_buffer *);
int dpe_vk_buffer_copy(struct dpe_vk_buffer *, void (*)(void *, bool), void *);
#endif
