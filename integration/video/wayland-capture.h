// Direct output-capture protocol adapter. The caller supplies an explicit,
// dedicated Wayland connection and owns dispatch, allocation and downstream use.
// No host connection, source substitution or capture starts implicitly.
#ifndef DPE_WAYLAND_CAPTURE_H
#define DPE_WAYLAND_CAPTURE_H
#include <stdbool.h>
#include <stdint.h>
#include <sys/types.h>
#include <wayland-client.h>

#define DPE_WL_FORMAT_LIMIT 256
struct dpe_wl_capture;
struct dpe_wl_format {
  uint32_t format;
  uint64_t modifier;
  bool dmabuf;
};
struct dpe_wl_constraints {
  uint64_t generation;
  uint32_t width, height;
  bool has_device;
  dev_t device; // Compare via DRM device identity, never primary/render dev_t equality.
  unsigned format_count;
  struct dpe_wl_format formats[DPE_WL_FORMAT_LIMIT];
};
struct dpe_wl_buffer {
  struct wl_buffer *handle;
  uint64_t generation;
  uint32_t width, height;
  struct dpe_wl_format format;
};
struct dpe_wl_frame {
  uint64_t generation, presentation_ns; // CLOCK_MONOTONIC, from the compositor.
  uint32_t transform;
  int32_t x, y, width, height; // Union of damage, in buffer coordinates.
};
struct dpe_wl_callbacks {
  void (*constraints)(void *data, const struct dpe_wl_constraints *constraints);
  // status=0: buffer is readable. Negative errno: failed, no image delivery.
  // EAGAIN is a temporary frame failure; retry on a later paced attempt.
  // The borrowed buffer descriptor/handle/storage must remain valid until this
  // callback, or until the dedicated connection has been disconnected on abort.
  // A successful capture does not release downstream encoder ownership.
  void (*frame)(void *data, const struct dpe_wl_buffer *buffer, const struct dpe_wl_frame *frame,
                int status);
  // Stop production, retain any outstanding buffer until frame completion or
  // connection teardown. Do not reinterpret cancellation as successful capture.
  void (*stopped)(void *data, int reason);
};
// Callbacks may submit another frame, but must not destroy this adapter or its
// display reentrantly. All calls/dispatch occur on the same thread.
struct dpe_wl_capture *dpe_wl_capture_new(struct wl_display *display, const char *output_name,
                                          const struct dpe_wl_callbacks *callbacks, void *data);
// Optional pre-start filter. Irrelevant formats do not consume the bounded
// accepted-format inventory; event metadata shape is still validated.
int dpe_wl_capture_set_format_filter(struct dpe_wl_capture *capture,
                                     bool (*accept)(void *data, uint32_t format, bool dmabuf));
// Call after two caller-managed registry roundtrips. Only the exact named
// wl_output is accepted; no first-monitor or window-to-monitor fallback.
int dpe_wl_capture_start(struct dpe_wl_capture *capture, bool paint_cursor);
// At most one outstanding frame; no wait, queue, mapping or conversion.
// Damage the whole destination because a recycled buffer can lag other buffers.
// A constraints failure rejects that generation until a new constraints batch.
int dpe_wl_capture_submit(struct dpe_wl_capture *capture, const struct dpe_wl_buffer *buffer);
int dpe_wl_capture_error(const struct dpe_wl_capture *capture);
// Destroy before disconnecting the dedicated display, then retire outstanding
// storage. After an aborted frame, never reuse its storage as a captured image.
void dpe_wl_capture_destroy(struct dpe_wl_capture *capture);
#endif
