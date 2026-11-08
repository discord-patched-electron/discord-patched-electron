// SPDX-License-Identifier: MIT
// Wire compatibility with nowrep/obs-vkcapture's capture.h. The upstream hook
// has one shared texture, implicit synchronization and no frame/FPS protocol.
#ifndef DPE_VKCAPTURE_COMPAT_H
#define DPE_VKCAPTURE_COMPAT_H
#include "vkcapture-wire.h"
#include <string.h>
struct dpe_obs_texture {
  uint8_t type, nfd;
  int32_t width, height, format, strides[4], offsets[4];
  uint64_t modifier;
  uint32_t winid;
  uint8_t flip;
  uint32_t color_space;
  uint8_t padding[65];
} __attribute__((packed));
struct dpe_obs_control {
  uint8_t capturing, no_modifiers, linear, map_host, uuid[16], padding[12];
} __attribute__((packed));
_Static_assert(sizeof(struct dpe_obs_texture) == 128, "OBS texture wire size");
_Static_assert(sizeof(struct dpe_obs_control) == 32, "OBS control wire size");
static inline bool dpe_obs_texture_packet(const struct dpe_obs_texture *input, unsigned descriptors,
                                          uint64_t generation, struct dpe_vk_packet *output) {
  if (input->type != 11 || input->nfd != descriptors || !descriptors || descriptors > 4 ||
      input->width <= 0 || input->height <= 0 || input->width > 8192 || input->height > 8192 ||
      input->flip > 1 || input->color_space != 0 || !generation)
    return false;
  *output = dpe_vk_packet(DPE_VK_TEXTURE);
  output->generation = generation;
  output->width = input->width;
  output->height = input->height;
  output->format = input->format;
  output->modifier = input->modifier;
  output->flags = input->flip ? DPE_VK_FLIP : 0;
  output->nfd = descriptors;
  memcpy(output->name, &input->winid, sizeof(input->winid));
  for (unsigned i = 0; i < descriptors; i++) {
    if (input->strides[i] <= 0 || input->strides[i] > 131072 || input->offsets[i] < 0)
      return false;
    output->strides[i] = input->strides[i];
    output->offsets[i] = input->offsets[i];
  }
  return true;
}
#endif
