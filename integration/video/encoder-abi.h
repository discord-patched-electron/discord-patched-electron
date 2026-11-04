#ifndef DPE_ENCODER_ABI_H
#define DPE_ENCODER_ABI_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

// Reviewed x86-64 SysV C interface, not Rust object layouts.
struct encoder_dimensions {
  uint32_t width, height;
};
struct encoder_session {
  uint32_t fps;
  struct encoder_dimensions maximum;
  uint32_t format, latency, keyframe_interval, keyframe_interval_ms, codec;
  const char *experiments;
};
struct encoder_options {
  uint32_t fps;
  struct encoder_dimensions dimensions;
  uint32_t bitrate_target, bitrate_maximum, keyframe_interval;
};
struct encoder_frame_info {
  uint8_t idr, eos, padding[6];
  uint64_t timestamp_ms;
  uint8_t want_recon, padding2[7];
};
struct encoder_plane {
  const uint8_t *data;
  size_t stride, width, height;
};
struct encoder_frame_meta {
  uint32_t width, height;
  uint64_t timestamp, capture_timestamp, ntp_timestamp;
  uint32_t rotation, padding;
  uint64_t submitted;
};
typedef void (*encoder_encoded_fn)(void *, const struct encoder_frame_meta *, uint64_t,
                                   const uint8_t *, size_t, const void *);
typedef void (*encoder_dropped_fn)(void *, const struct encoder_frame_meta *);

// A borrowed, single-object NV12 DMA-BUF. The lease prevents producer reuse
// until the backend has finished reading it. Import does not map its pixels.
struct encoder_nv12 {
  uint32_t width, height;
  int32_t fd;
  uint32_t size, offset[2], stride[2];
  uint64_t modifier;
};
_Static_assert(sizeof(struct encoder_session) == 40, "Reviewed SessionOptions ABI");
_Static_assert(sizeof(struct encoder_options) == 24, "Reviewed EncodeOptions ABI");
_Static_assert(sizeof(struct encoder_frame_info) == 24, "Reviewed FrameInfo ABI");
_Static_assert(sizeof(struct encoder_plane) == 32, "Reviewed I420 plane ABI");
_Static_assert(sizeof(struct encoder_frame_meta) == 48, "Reviewed FrameMeta ABI");

// The isolated encoder library exports only this versioned C table. FFmpeg's
// symbols stay private so they cannot bind to Discord/Electron's FFmpeg copy.
#define DPE_HEVC_ABI 1
struct dpe_hevc_api {
  uint32_t abi, size;
  uint32_t (*probe)(const char *node);
  void *(*create)(const char *node, struct encoder_session, struct encoder_options, void *,
                  encoder_encoded_fn, encoder_dropped_fn);
  bool (*update)(void *, struct encoder_options);
  bool (*i420)(void *, const struct encoder_frame_meta *, struct encoder_frame_info,
               const struct encoder_plane[3]);
  // Consumes the lease on success AND failure, exactly once.
  bool (*nv12)(void *, const struct encoder_frame_meta *, struct encoder_frame_info,
               const struct encoder_nv12 *, void *, void (*)(void *));
  void (*destroy)(void *);
  uint32_t (*vendor)(void *);
};
#endif
