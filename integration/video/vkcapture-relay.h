// SPDX-License-Identifier: MIT
#ifndef DPE_VKCAPTURE_RELAY_H
#define DPE_VKCAPTURE_RELAY_H
#include "vkcapture-wire.h"
#include <pipewire/pipewire.h>
// Stable PipeWire 1.2 ABI, also builds against older development headers.
enum { DPE_RELAY_META = 9, DPE_RELAY_DATA = 5, DPE_RELAY_META_TYPE = 7 };
struct dpe_relay_timeline {
  uint32_t flags, padding;
  uint64_t acquire_point, release_point;
};
struct dpe_vk_relay;
// Opaque Rust handle. Calls and event-loop callbacks use the creating thread;
// the loop outlives the relay. Buffer metadata lasts through submit, while
// completion can outlive PipeWire's buffer storage. Destroy accepts stale handles.
// Probe synchronization UAPI without GPU work or importing game storage.
bool dpe_vk_relay_supported(int drm);
struct dpe_vk_relay *dpe_vk_relay_create(int, struct pw_loop *, struct spa_buffer *,
                                         const struct dpe_vk_packet *, const int[4]);
void dpe_vk_relay_destroy(struct dpe_vk_relay *);
uint64_t dpe_vk_relay_generation(const struct dpe_vk_relay *);
// Takes ownership of acquire_fd, including on failure; -1 means already complete.
// Forward the hook's sync_file to the consumer without a helper-side wait.
// Completion requires successful acquire and the consumer's GPU release point,
// independently of when PipeWire returns the buffer.
int dpe_vk_relay_submit(struct dpe_vk_relay *, int acquire_fd, void (*)(void *, bool), void *);
uint32_t dpe_vk_relay_format(uint32_t);
#endif
