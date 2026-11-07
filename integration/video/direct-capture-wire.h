// SPDX-License-Identifier: MIT
// Same-user, message-framed GPU transport. Descriptors travel with SCM_RIGHTS;
// no image bytes, PipeWire nodes or process-local pointers cross this boundary.
#ifndef DPE_DIRECT_CAPTURE_WIRE_H
#define DPE_DIRECT_CAPTURE_WIRE_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#define DPE_DIRECT_MAGIC UINT32_C(0x44504544)
#define DPE_DIRECT_ABI 1
#define DPE_DIRECT_NODE UINT32_C(0xfffffffe)
#define DPE_DIRECT_FDS 9
enum { DPE_DIRECT_DMABUF = 0, DPE_DIRECT_CURSOR_BITMAP = 1 };
enum dpe_direct_type {
  DPE_DIRECT_START = 1,
  DPE_DIRECT_FRAME,
  DPE_DIRECT_RELEASE,
  DPE_DIRECT_RATE,
  DPE_DIRECT_END,
  DPE_DIRECT_ERROR
};
struct dpe_direct_rect {
  int32_t x, y;
  uint32_t width, height;
};
struct dpe_direct_image {
  uint32_t width, height, format, planes;
  uint32_t storage, padding;
  uint64_t modifier;
  uint32_t fd[4], offset[4], stride[4];
  struct dpe_direct_rect source, destination;
};
struct dpe_direct_packet {
  uint32_t magic, abi, size, type;
  uint64_t generation, sequence, timestamp_ns;
  uint32_t width, height, fps, descriptors;
  uint32_t device_major, device_minor;
  struct dpe_direct_image image, cursor;
  // Zero means no explicit fence; otherwise one plus its descriptor index.
  uint32_t acquire, reserved;
};
static inline struct dpe_direct_packet dpe_direct_packet(unsigned type) {
  struct dpe_direct_packet packet;
  memset(&packet, 0, sizeof(packet));
  packet.magic = DPE_DIRECT_MAGIC;
  packet.abi = DPE_DIRECT_ABI;
  packet.size = sizeof(packet);
  packet.type = type;
  return packet;
}
static inline bool dpe_direct_header(const struct dpe_direct_packet *p, size_t size) {
  return size == sizeof(*p) && p->size == sizeof(*p) && p->magic == DPE_DIRECT_MAGIC &&
         p->abi == DPE_DIRECT_ABI && p->type >= DPE_DIRECT_START && p->type <= DPE_DIRECT_ERROR &&
         !p->reserved && p->descriptors <= DPE_DIRECT_FDS;
}
// Success transfers descriptor ownership to the caller. Failure closes all received descriptors.
int dpe_direct_receive(int, struct dpe_direct_packet *, int[DPE_DIRECT_FDS], unsigned *);
int dpe_direct_receive_raw(int, void *, size_t, int *, unsigned, unsigned *);
bool dpe_direct_send(int, const struct dpe_direct_packet *, const int *, unsigned);
void dpe_direct_close(int *, unsigned);
bool dpe_direct_peer(int);
#endif
