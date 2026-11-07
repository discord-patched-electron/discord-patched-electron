// ABI-only backend; no codec, device, pixels or fake production install hooks.
#include "hevc-fixture.h"
#include <assert.h>
#include <stdlib.h>
#include <string.h>
#ifndef DPE_FIXTURE_ABI
#define DPE_FIXTURE_ABI DPE_HEVC_ABI
#endif
struct encoder {
  struct fixture_context *context;
  encoder_encoded_fn encoded;
};
static void *create(const char *node, struct encoder_session session,
                    struct encoder_options options, void *context, encoder_encoded_fn encoded,
                    encoder_dropped_fn dropped) {
  assert(!strcmp(node, "/test/renderD128") && session.codec <= 2 && options.fps == 60);
  assert(options.dimensions.width == 16 && options.dimensions.height == 16 && encoded && dropped);
  struct encoder *encoder = malloc(sizeof(*encoder));
  assert(encoder);
  encoder->context = context;
  encoder->encoded = encoded;
  atomic_fetch_add(&encoder->context->created, 1);
  return encoder;
}
static bool update(void *handle, struct encoder_options options) {
  struct fixture_context *context = ((struct encoder *)handle)->context;
  assert(options.fps == 30);
  pthread_mutex_lock(&context->mutex);
  context->update_entered = context->update_active = true;
  pthread_cond_broadcast(&context->condition);
  while (!context->finish_update)
    pthread_cond_wait(&context->condition, &context->mutex);
  context->update_active = false;
  pthread_mutex_unlock(&context->mutex);
  return true;
}
static void metadata(const struct encoder_frame_meta *meta, struct encoder_frame_info info) {
  assert(meta->timestamp == 123456 && info.timestamp_ms == 789);
  assert(info.idr && info.want_recon && !info.eos);
}
static bool i420(void *handle, const struct encoder_frame_meta *meta,
                 struct encoder_frame_info info, const struct encoder_plane planes[3]) {
  struct encoder *encoder = handle;
  struct fixture_context *context = encoder->context;
  metadata(meta, info);
  assert(planes[0].data == context->pixels && planes[0].stride == 16 && planes[0].width == 16);
  assert(planes[1].data == context->pixels + 256 && planes[2].data == context->pixels + 320);
  assert(planes[1].width == 8 && planes[2].height == 8 && planes[1].stride == 8);
  atomic_fetch_add(&context->submitted, 1);
  // Callback may free both encoder and context; never touch either afterwards.
  if (context->reenter)
    encoder->encoded(context, meta, 0, (const uint8_t *)"x", 1, NULL);
  return true;
}
static bool nv12(void *handle, const struct encoder_frame_meta *meta,
                 struct encoder_frame_info info, const struct encoder_nv12 *view, void *lease,
                 void (*release)(void *)) {
  struct fixture_context *context = ((struct encoder *)handle)->context;
  metadata(meta, info);
  assert(view->width == 16 && view->height == 16 && view->fd == 123 && view->size == 384);
  assert(view->stride[0] == 16 && view->stride[1] == 16 && view->offset[1] == 256);
  assert(view->modifier == 0);
  atomic_fetch_add(&context->leases, 1);
  bool result = !context->fail_import;
  release(lease);
  return result;
}
static void destroy(void *handle) {
  struct encoder *encoder = handle;
  assert(!encoder->context->update_active);
  atomic_fetch_add(&encoder->context->destroyed, 1);
  free(encoder);
}
static uint32_t probe(const char *node) {
  assert(node);
  return 0;
}
static uint32_t vendor(void *handle) {
  assert(handle);
  return 1;
}
uint32_t dpe_encoder_hardware_scope(void *handle) {
  assert(handle);
  return 1;
}
const struct dpe_hevc_api *dpe_hevc_api(void) {
  static const struct dpe_hevc_api api = {
      DPE_FIXTURE_ABI, sizeof(api), probe, create, update, i420, nv12, destroy, vendor};
  return &api;
}
