// Test-only synthetic DMA-BUF producer. Never used by the installed client.
#ifndef DPE_GPU_SOURCE_H
#define DPE_GPU_SOURCE_H
#include <pipewire/pipewire.h>
struct gpu_source;
struct gpu_frame;
struct gpu_source *gpu_source_create(const char *node, struct pw_loop *loop, uint32_t width,
                                     uint32_t height, unsigned pattern);
void gpu_source_destroy(struct gpu_source *gpu);
void gpu_source_set_pattern(struct gpu_source *gpu, unsigned pattern);
struct gpu_frame *gpu_frame_create(struct gpu_source *gpu, struct spa_data *data);
void gpu_frame_destroy(struct gpu_frame *frame);
// Completion runs on the PipeWire main loop after the GPU fence signals.
// A frame stays producer-owned until completion; at most one is in flight.
int gpu_frame_render(struct gpu_frame *frame, uint32_t stamp, uint8_t crc, uint32_t sequence,
                     void (*complete)(void *, bool), void *opaque);
#endif
