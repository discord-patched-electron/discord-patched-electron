// Synthetic native module: real PLT/GOT relocations and reviewed layout borrows.
#include "hevc-fixture.h"
#include <assert.h>
#include <string.h>
static unsigned counts[6];
static uint8_t pixels[384];
static const uint64_t *descriptor;
unsigned *fixture_counts(void) { return counts; }
uint8_t *fixture_pixels(void) { return pixels; }
void fixture_descriptor(const uint64_t *value) { descriptor = value; }
const void *fixture19PooledVulkanSurface10descriptor(const void *surface) {
  assert(surface);
  return descriptor;
}
void fixture13capture_linux10LinuxFrameE9drop_slow(void **arc) {
  assert(*arc);
  *arc = NULL;
  counts[4]++;
}
void *cc_video_frame_clone_i420(void *frame, const uint8_t **data, size_t *size, size_t *sy,
                                size_t *suv, size_t *u, size_t *v) {
  assert(frame);
  *data = pixels;
  *size = sizeof(pixels);
  *sy = 16;
  *suv = 8;
  *u = 256;
  *v = 320;
  return pixels;
}
void cc_i420_buffer_destroy(void *lease) {
  assert(lease == pixels);
  counts[5]++;
}
bool cc_encoder_add_frame_native(void *handle, const void *meta, void *frame,
                                 struct encoder_frame_info info) {
  (void)meta;
  (void)frame;
  (void)info;
  assert(handle);
  counts[0]++;
  return true;
}
bool cc_encoder_add_frame_i420(void *handle, const void *meta, struct encoder_frame_info info,
                               struct encoder_plane y, struct encoder_plane u,
                               struct encoder_plane v) {
  (void)meta;
  (void)info;
  (void)y;
  (void)u;
  (void)v;
  assert(handle);
  counts[1]++;
  return true;
}
bool cc_encoder_supports_recon_frame(void *handle) {
  assert(handle);
  counts[2]++;
  return true;
}
uint32_t cc_encoder_vendor(void *handle) {
  assert(handle);
  counts[3]++;
  return 2;
}
void cc_probe_gpu_encoder(const char *helper, void *support) {
  (void)helper;
  assert(support);
}
bool fixture_native(void *handle, const void *meta, void *frame, struct encoder_frame_info info) {
  return cc_encoder_add_frame_native(handle, meta, frame, info);
}
bool fixture_cpu(void *handle, const void *meta, struct encoder_frame_info info,
                 struct encoder_plane y, struct encoder_plane u, struct encoder_plane v) {
  return cc_encoder_add_frame_i420(handle, meta, info, y, u, v);
}
bool fixture_recon(void *handle) { return cc_encoder_supports_recon_frame(handle); }
uint32_t fixture_vendor(void *handle) { return cc_encoder_vendor(handle); }
void fixture_probe(const char *helper, void *support) { cc_probe_gpu_encoder(helper, support); }
