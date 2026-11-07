// SPDX-License-Identifier: MIT
#ifndef DPE_CAPTURE_DELIVERY_H
#define DPE_CAPTURE_DELIVERY_H
#include "capture-abi.h"
struct delivery_probe;
// Read-only diagnostics; native storage and leases remain owned by Rust.
struct dpe_delivery_snapshot {
  uint64_t id, auto_fast, fast, log, requested_rate, events, stopping;
  uint64_t wake_serial, frame_serial, callbacks, fresh, duplicate, unknown;
  uint64_t forwarded, dropped, heartbeats;
  void *lease, *identity, *framebuffer;
  uint64_t next_unknown_ns;
  void *data;
  uint64_t last_forward_ns;
};
bool dpe_delivery_inspect(struct delivery_probe *, struct dpe_delivery_snapshot *);
struct dpe_delivery_functions {
  clone_i420_fn clone;
  release_frame_fn release_planar, release_frame;
};
struct delivery_probe *dpe_delivery_create(uint32_t, const struct capture_options *, bool,
                                           struct dpe_delivery_functions);
bool dpe_delivery_adapt(struct delivery_probe *, const struct capture_options *,
                        struct capture_options *);
int32_t dpe_delivery_update(struct delivery_probe *, void *, const struct capture_options *,
                            update_fn);
void dpe_delivery_destroy(struct delivery_probe *);
void dpe_delivery_wake(struct delivery_probe *, bool);
void dpe_delivery_frame(void *, uint32_t, const char *, void *);
void dpe_delivery_frame_at(void *, uint32_t, const char *, void *, uint64_t);
typedef void *(*buffer_read_fn)(void *);
typedef void (*buffer_write_fn)(void *, void *);
typedef void *(*receive_fn)(void *, void *, uint64_t, uint32_t);
typedef bool (*overdue_fn)(void *);
bool dpe_delivery_configure_wake(buffer_read_fn, buffer_write_fn, receive_fn, overdue_fn);
bool dpe_delivery_enable_wake(void);
void *dpe_delivery_read(void *);
void dpe_delivery_write(void *, void *);
void *dpe_delivery_receive(void *, void *, uint64_t, uint32_t);
bool dpe_delivery_overdue(void *);
#endif
