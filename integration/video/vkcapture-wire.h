// SPDX-License-Identifier: MIT
// DPE-only, local same-user SOCK_SEQPACKET transport. Never sent to OBS's socket.
#ifndef DPE_VKCAPTURE_WIRE_H
#define DPE_VKCAPTURE_WIRE_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#define DPE_VK_MAGIC 0x44504547u
#define DPE_VK_VERSION 1
enum dpe_vk_type {
  DPE_VK_HELLO = 1,
  DPE_VK_TEXTURE,
  DPE_VK_FRAME,
  DPE_VK_STOP,
  DPE_VK_CONTROL,
  DPE_VK_RELEASE,
  DPE_VK_UNSUPPORTED
};
enum {
  DPE_VK_FLIP = 1,
  DPE_VK_CAPTURE = 2,
  DPE_VK_SYNC_FD = 4,
  DPE_VK_DIRECT = 8,
  DPE_VK_PAUSE = 16
}; // HELLO capability, not a CONTROL flag.
struct dpe_vk_packet {
  uint32_t magic;
  uint16_t version, type;
  uint64_t generation, sequence, pts;
  uint32_t width, height, format, flags;
  uint64_t modifier;
  uint32_t strides[4], offsets[4], fps, nfd;
  uint8_t uuid[16];
  char name[16];
};
_Static_assert(sizeof(struct dpe_vk_packet) == 128, "DPE game protocol size");
static inline struct dpe_vk_packet dpe_vk_packet(unsigned type) {
  return (struct dpe_vk_packet){.magic = DPE_VK_MAGIC, .version = DPE_VK_VERSION, .type = type};
}
static inline bool dpe_vk_valid(const struct dpe_vk_packet *p, size_t size) {
  return size == sizeof(*p) && p->magic == DPE_VK_MAGIC && p->version == DPE_VK_VERSION &&
         p->type >= DPE_VK_HELLO && p->type <= DPE_VK_UNSUPPORTED;
}
// Texture packets previously left name zeroed. Carry the exact X11 surface
// identity there without changing packet size or old-peer compatibility.
static inline uint32_t dpe_vk_window(const struct dpe_vk_packet *p) {
  uint32_t window;
  memcpy(&window, p->name, sizeof(window));
  return window;
}
#endif
