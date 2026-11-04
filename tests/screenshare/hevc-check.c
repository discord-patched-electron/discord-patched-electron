// Exercise adapter ownership and the reviewed by-value ABI without GPU access.
#include "../../integration/video/encoder-hevc.c"
#include "../../integration/video/hevc-level.h"
#include <assert.h>

static unsigned created, destroyed, submitted, leases, linux_drops, cpu_drops, forwarded;
static bool fail_import, update_entered, finish_update, update_active;
static pthread_mutex_t test_mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t test_condition = PTHREAD_COND_INITIALIZER;
static uint8_t pixels[384];
static uint64_t descriptor[13], planes[6];
static int dma_fd = 123;
static int fake_handle;

static void encoded(void *context, const struct encoder_frame_meta *meta, uint64_t info,
                    const uint8_t *data, size_t size, const void *recon) {
  (void)context;
  (void)meta;
  (void)info;
  (void)data;
  (void)size;
  (void)recon;
}
static void dropped(void *context, const struct encoder_frame_meta *meta) {
  (void)context;
  (void)meta;
}
static void *create(const char *node, struct encoder_session session,
                    struct encoder_options options, void *context, encoder_encoded_fn callback,
                    encoder_dropped_fn drop) {
  assert(!strcmp(node, "/test/renderD128") && (session.codec == 1 || session.codec == 2) &&
         options.fps == 60);
  assert(options.dimensions.width == 16 && options.dimensions.height == 16);
  assert(context == &created && callback == encoded && drop == dropped);
  created++;
  return &fake_handle;
}
static bool update(void *handle, struct encoder_options options) {
  assert(handle == &fake_handle && options.fps == 30);
  pthread_mutex_lock(&test_mutex);
  update_entered = update_active = true;
  pthread_cond_broadcast(&test_condition);
  while (!finish_update)
    pthread_cond_wait(&test_condition, &test_mutex);
  update_active = false;
  pthread_mutex_unlock(&test_mutex);
  return true;
}
static bool i420(void *handle, const struct encoder_frame_meta *meta,
                 struct encoder_frame_info info, const struct encoder_plane input[3]) {
  assert(handle == &fake_handle && meta->timestamp == 123456 && info.timestamp_ms == 789);
  assert(info.idr && info.want_recon && !info.eos && input[0].data == pixels);
  assert(input[0].stride == 16 && input[0].width == 16 && input[0].height == 16);
  assert(input[1].stride == 8 && input[1].data == pixels + 256);
  assert(input[2].stride == 8 && input[2].data == pixels + 320);
  assert(input[1].width == 8 && input[2].height == 8);
  submitted++;
  return true;
}
static bool nv12(void *handle, const struct encoder_frame_meta *meta,
                 struct encoder_frame_info info, const struct encoder_nv12 *view, void *lease,
                 void (*release_lease)(void *)) {
  assert(handle == &fake_handle && meta->timestamp == 123456 && info.timestamp_ms == 789);
  assert(view->width == 16 && view->height == 16 && view->fd == dma_fd && view->size == 384);
  assert(view->stride[0] == 16 && view->stride[1] == 16 && view->offset[1] == 256);
  assert(view->modifier == 0);
  leases++;
  release_lease(lease);
  return !fail_import;
}
static void destroy(void *handle) {
  assert(handle == &fake_handle && !update_active);
  destroyed++;
}
static uint32_t vendor(void *handle) {
  assert(handle == &fake_handle);
  return 1;
}
static void no_probe(void) {}
static const void *describe(const void *surface) {
  assert(surface);
  return descriptor;
}
static void drop_linux(void *reference) {
  assert(*(void **)reference);
  linux_drops++;
}
static void *clone_cpu(void *frame, const uint8_t **data, size_t *size, size_t *sy, size_t *suv,
                       size_t *u, size_t *v) {
  assert(frame);
  *data = pixels;
  *size = sizeof(pixels);
  *sy = 16;
  *suv = 8;
  *u = 256;
  *v = 320;
  return pixels;
}
static void drop_cpu(void *lease) {
  assert(lease == pixels);
  cpu_drops++;
}
static bool native_forward(void *handle, const void *meta, void *frame,
                           struct encoder_frame_info info) {
  (void)meta;
  (void)frame;
  (void)info;
  assert(handle != &fake_handle);
  forwarded++;
  return true;
}
static void *updating(void *unused) {
  (void)unused;
  assert(dpe_hevc_update(&fake_handle, (struct encoder_options){.fps = 30}));
  return NULL;
}
static void *destroying(void *unused) {
  (void)unused;
  assert(dpe_hevc_destroy(&fake_handle));
  return NULL;
}

int main(void) {
  assert(dpe_hevc_level(1920, 1080, 30, 9000000) == 120);
  assert(dpe_hevc_level(1920, 1080, 60, 9000000) == 123);
  assert(dpe_hevc_level(1920, 1080, 120, 9000000) == 150);
  assert(dpe_hevc_level(3840, 2160, 60, 9000000) == 153);
  assert(dpe_hevc_level(3840, 2160, 120, 9000000) == 156);
  assert(dpe_hevc_level(1920, 1080, 60, 32000000) == 153);
  assert(!dpe_hevc_level(1920, 1080, 0, 9000000));
  assert(!dpe_hevc_level(UINT32_MAX, UINT32_MAX, UINT32_MAX, 9000000));
  static const struct dpe_hevc_api api = {.create = create,
                                          .update = update,
                                          .i420 = i420,
                                          .nv12 = nv12,
                                          .destroy = destroy,
                                          .vendor = vendor};
  backend = &api;
  installed = 1;
  strcpy(devices[0], "/test/renderD128");
  pthread_once(&probe_once, no_probe);
  surface_descriptor = describe;
  drop_linux_frame = drop_linux;
  clone_i420 = clone_cpu;
  drop_i420 = drop_cpu;
  original_native = native_forward;
  struct encoder_session session = {.codec = 1};
  struct encoder_options options = {.fps = 60, .dimensions = {16, 16}};
  void *handle = NULL;
  assert(dpe_hevc_create(0, &created, encoded, dropped, &handle, session, options) == -1);
  assert(!dpe_hevc_create(5, &created, encoded, dropped, NULL, session, options));
  assert(dpe_hevc_create(5, &created, encoded, dropped, &handle, session, options) == 1);
  assert(created == 1 && dpe_hevc_owned(handle) && dpe_hevc_active());
  assert(encoder_vendor(handle) == 1 && !supports_recon(handle));
  struct encoder_frame_meta meta = {.width = 16, .height = 16, .timestamp = 123456};
  struct encoder_frame_info info = {.idr = 1, .timestamp_ms = 789, .want_recon = 1};
  struct encoder_plane y = {pixels, 16, 16, 16}, u = {pixels + 256, 8, 8, 8},
                       v = {pixels + 320, 8, 8, 8};
  assert(cpu_frame(handle, &meta, info, y, u, v));
  uint64_t frame[4] = {0};
  assert(native_frame(handle, &meta, frame, info));
  assert(cpu_drops == 1 && submitted == 2);
  uint64_t arc[4] = {1, 1, 0, 0}, pooled[32] = {0};
  frame[0] = 1;
  frame[1] = (uintptr_t)arc;
  arc[2] = (uintptr_t)pooled;
  planes[2] = planes[5] = 16;
  planes[4] = 256;
  descriptor[1] = (uintptr_t)planes;
  descriptor[2] = 2;
  descriptor[3] = 0x3231564e;
  descriptor[6] = descriptor[7] = 16;
  descriptor[8] = 384;
  descriptor[10] = descriptor[12] = 1;
  descriptor[11] = (uintptr_t)&dma_fd;
  assert(native_frame(handle, &meta, frame, info));
  assert(arc[0] == 1 && leases == 1 && linux_drops == 0);
  fail_import = true;
  assert(!native_frame(handle, &meta, frame, info));
  assert(arc[0] == 1 && leases == 2);
  // Descriptor None must use the native CPU lease, not call a panicking Rust accessor.
  pooled[(16 + 0xb0) / 8] = 2;
  assert(native_frame(handle, &meta, frame, info));
  assert(cpu_drops == 2 && submitted == 3);
  release_gpu_frame(arc);
  assert(arc[0] == 0 && linux_drops == 1);
  assert(native_frame(&created, &meta, frame, info) && forwarded == 1);
  pthread_t first, second;
  assert(!pthread_create(&first, NULL, updating, NULL));
  pthread_mutex_lock(&test_mutex);
  while (!update_entered)
    pthread_cond_wait(&test_condition, &test_mutex);
  assert(!pthread_create(&second, NULL, destroying, NULL));
  finish_update = true;
  pthread_cond_broadcast(&test_condition);
  pthread_mutex_unlock(&test_mutex);
  assert(!pthread_join(first, NULL) && !pthread_join(second, NULL));
  assert(destroyed == 1 && !dpe_hevc_owned(handle) && !dpe_hevc_active());
  assert(!dpe_hevc_destroy(handle) && !dpe_hevc_update(handle, options));
  uint64_t stats[6];
  dpe_hevc_stats(stats);
  assert(stats[0] == 1 && stats[2] == 2 && stats[3] == 3 && stats[4] == 2 && stats[5] == 1);
  // A distribution may disable patented HEVC while still supporting AV1.
  // AV1 must not depend on a successful HEVC capability probe.
  devices[0][0] = 0;
  strcpy(av1_device, "/test/renderD128");
  session.codec = 2;
  assert(dpe_hevc_create(5, &created, encoded, dropped, &handle, session, options) == -1);
  assert(dpe_av1_create(5, &created, encoded, dropped, &handle, session, options) == 1);
  assert(cpu_frame(handle, &meta, info, y, u, v));
  dpe_av1_stats(stats);
  assert(stats[3] == 1 && stats[2] == 0 && stats[5] == 0);
  assert(dpe_hevc_destroy(handle) && destroyed == 2 && created == 2);
  return 0;
}
