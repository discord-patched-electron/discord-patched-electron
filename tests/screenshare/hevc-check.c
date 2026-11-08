// Exercise the Rust adapter through real ELF linkage and the by-value C ABI.
#define _GNU_SOURCE
#include "../../integration/video/encoder-hevc.h"
#include "../../integration/video/hevc-level.h"
#include "hevc-fixture.h"
#include <assert.h>
#include <dlfcn.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
static int direct_frame, direct_lease;
static unsigned direct_releases;
bool dpe_direct_frame_owned(const void *frame) { return frame == &direct_frame; }
bool dpe_direct_frame_view(void *frame, struct encoder_nv12 *view, void **lease,
                           const char **node) {
  if (!dpe_direct_frame_owned(frame))
    return false;
  *view = (struct encoder_nv12){
      .width = 16, .height = 16, .fd = 123, .size = 384, .stride = {16, 16}, .offset = {0, 256}};
  *lease = &direct_lease;
  if (node)
    *node = "/test/renderD128";
  return true;
}
void dpe_direct_frame_release(void *lease) {
  assert(lease == &direct_lease);
  direct_releases++;
}
static void encoded(void *opaque, const struct encoder_frame_meta *meta, uint64_t info,
                    const uint8_t *data, size_t size, const void *recon) {
  (void)meta;
  (void)info;
  (void)data;
  (void)size;
  (void)recon;
  struct fixture_context *context = opaque;
  assert(context->reenter && dpe_hevc_owned(context->wrapper));
  assert(dpe_hevc_update(context->wrapper, (struct encoder_options){.fps = 30}));
  assert(dpe_hevc_destroy(context->wrapper));
  assert(!dpe_hevc_owned(context->wrapper));
}
static void dropped(void *context, const struct encoder_frame_meta *meta) {
  (void)context;
  (void)meta;
}
static void *updating(void *opaque) {
  struct fixture_context *context = opaque;
  assert(dpe_hevc_update(context->wrapper, (struct encoder_options){.fps = 30}));
  return NULL;
}
static void *destroying(void *opaque) {
  struct fixture_context *context = opaque;
  atomic_store(&context->destroy_started, true);
  assert(dpe_hevc_destroy(context->wrapper));
  atomic_store(&context->destroy_finished, true);
  return NULL;
}
#define SYMBOL(name, type)                                                                         \
  type name = (type)dlsym(module, "fixture_" #name);                                               \
  assert(name)
int main(int argc, char **argv) {
  alarm(10);
  assert(argc == 21);
  void *module = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
  assert(module);
  uint32_t offsets[19];
  for (unsigned i = 0; i < 19; i++)
    offsets[i] = strtoul(argv[i + 2], NULL, 10);
  // All preflight errors must leave every original slot untouched.
  uint32_t bad[19];
  memcpy(bad, offsets, sizeof(bad));
  bad[0] = UINT32_MAX;
  assert(!dpe_hevc_install(argv[1], bad, NULL, 0));
  memcpy(bad, offsets, sizeof(bad));
  bad[15]++;
  assert(!dpe_hevc_install(argv[1], bad, NULL, 0));
  assert(dpe_hevc_install(argv[1], offsets, NULL, 0));
  assert(dpe_hevc_install(argv[1], offsets, NULL, 0));
  typedef bool (*native_fn)(void *, const void *, void *, struct encoder_frame_info);
  typedef bool (*cpu_fn)(void *, const void *, struct encoder_frame_info, struct encoder_plane,
                         struct encoder_plane, struct encoder_plane);
  typedef uint32_t (*vendor_fn)(void *);
  typedef bool (*recon_fn)(void *);
  typedef uint8_t *(*pixels_fn)(void);
  typedef unsigned *(*counts_fn)(void);
  typedef void (*descriptor_fn)(const uint64_t *);
  SYMBOL(native, native_fn);
  SYMBOL(cpu, cpu_fn);
  SYMBOL(vendor, vendor_fn);
  SYMBOL(recon, recon_fn);
  SYMBOL(pixels, pixels_fn);
  SYMBOL(counts, counts_fn);
  SYMBOL(descriptor, descriptor_fn);
  if (getenv("DPE_FIXTURE_UNAVAILABLE")) {
    uint64_t stats[6];
    dpe_hevc_stats(stats);
    assert(!stats[0] && !dpe_hevc_active());
    assert(vendor((void *)42) == 2 && recon((void *)42));
    dlclose(module);
    return 0;
  }
  struct fixture_context context = {.pixels = pixels(),
                                    .mutex = PTHREAD_MUTEX_INITIALIZER,
                                    .condition = PTHREAD_COND_INITIALIZER};
  struct encoder_session session = {.codec = 1};
  struct encoder_options options = {.fps = 60, .dimensions = {16, 16}};
  void *handle = NULL;
  assert(dpe_direct_encoder_create(0, &direct_frame, &context, encoded, dropped, &handle, session,
                                   options) == 0);
  assert(dpe_direct_encoder_create(5, &direct_frame, &context, encoded, dropped, &handle, session,
                                   options) == 1);
  context.wrapper = handle;
  assert(dpe_hevc_owned(handle) && dpe_hevc_active() && vendor(handle) == 1 && !recon(handle));
  struct encoder_frame_meta meta = {.width = 16, .height = 16, .timestamp = 123456};
  struct encoder_frame_info info = {.idr = 1, .timestamp_ms = 789, .want_recon = 1};
  struct encoder_plane y = {context.pixels, 16, 16, 16}, u = {context.pixels + 256, 8, 8, 8},
                       v = {context.pixels + 320, 8, 8, 8};
  assert(cpu(handle, &meta, info, y, u, v));
  uint64_t frame[4] = {0}, arc[4] = {1, 1, 0, 0}, pooled[32] = {0},
           planes[6] = {0, 0, 16, 0, 256, 16}, desc[13] = {0};
  assert(native(handle, &meta, frame, info));
  assert(counts()[5] == 1 && context.submitted == 2);
  frame[0] = 1;
  frame[1] = (uintptr_t)arc;
  arc[2] = (uintptr_t)pooled;
  int fd = 123;
  desc[1] = (uintptr_t)planes;
  desc[2] = 2;
  desc[3] = 0x3231564e;
  desc[6] = desc[7] = 16;
  desc[8] = 384;
  desc[10] = desc[12] = 1;
  desc[11] = (uintptr_t)&fd;
  descriptor(desc);
  assert(native(handle, &meta, frame, info) && arc[0] == 1 && context.leases == 1);
  context.fail_import = true;
  assert(!native(handle, &meta, frame, info) && arc[0] == 1 && context.leases == 2);
  pooled[(16 + 0xb0) / 8] = 2;
  assert(native(handle, &meta, frame, info) && counts()[5] == 2 && context.submitted == 3);
  assert(native((void *)42, &meta, frame, info) && counts()[0] == 1);
  assert(cpu((void *)42, &meta, info, y, u, v) && counts()[1] == 1);
  assert(vendor((void *)42) == 2 && recon((void *)42));
  pthread_t first, second;
  assert(!pthread_create(&first, NULL, updating, &context));
  pthread_mutex_lock(&context.mutex);
  while (!context.update_entered)
    pthread_cond_wait(&context.condition, &context.mutex);
  assert(!pthread_create(&second, NULL, destroying, &context));
  while (!atomic_load(&context.destroy_started)) {
    struct timespec tick = {.tv_nsec = 1000000};
    nanosleep(&tick, NULL);
  }
  struct timespec pause = {.tv_nsec = 20000000};
  nanosleep(&pause, NULL);
  assert(!atomic_load(&context.destroy_finished) && !atomic_load(&context.destroyed));
  context.finish_update = true;
  pthread_cond_broadcast(&context.condition);
  pthread_mutex_unlock(&context.mutex);
  assert(!pthread_join(first, NULL) && !pthread_join(second, NULL));
  assert(context.destroyed == 1 && !dpe_hevc_owned(handle) && !dpe_hevc_active());
  assert(!dpe_hevc_destroy(handle) && !dpe_hevc_update(handle, options));
  assert(!native(handle, &meta, frame, info) && !cpu(handle, &meta, info, y, u, v));
  assert(vendor(handle) == 4 && !recon(handle));
  uint64_t stats[6];
  dpe_hevc_stats(stats);
  assert(stats[0] == 1 && stats[2] == 2 && stats[3] == 3 && stats[4] == 2 && stats[5] == 1);
  context.fail_import = false;
  session.codec = 2;
  assert(dpe_direct_encoder_create(6, &direct_frame, &context, encoded, dropped, &handle, session,
                                   options) == 1);
  assert(native(handle, &meta, &direct_frame, info) && direct_releases == 3 &&
         dpe_direct_encoded_inputs() == 1);
  assert(!native((void *)42, &meta, &direct_frame, info) && counts()[0] == 1);
  assert(dpe_hevc_destroy(handle));
  dpe_av1_stats(stats);
  assert(stats[1] == 1 && stats[2] == 1);
  // Same-thread update and destruction from an encoded callback must finish.
  session.codec = 1;
  context.reenter = true;
  assert(dpe_direct_encoder_create(5, &direct_frame, &context, encoded, dropped, &handle, session,
                                   options) == 1);
  context.wrapper = handle;
  assert(cpu(handle, &meta, info, y, u, v));
  assert(context.created == 3 && context.destroyed == 3 && !dpe_hevc_active());
  assert(!dpe_hevc_destroy(handle));
  void *retired = (void *)(uintptr_t)UINT64_C(0x50e1000000000011);
  assert(!native((void *)42, &meta, retired, info) && counts()[0] == 1);
  assert(vendor(retired) == 4 && !recon(retired));
  // Registry capacity is hard-bounded and rejected creation releases its backend.
  context.reenter = false;
  void *bounded[128];
  for (unsigned i = 0; i < 128; i++) {
    assert(dpe_direct_encoder_create(5, &direct_frame, &context, encoded, dropped, &bounded[i],
                                     session, options) == 1);
    assert(bounded[i] != handle);
  }
  void *overflow = (void *)1;
  assert(!dpe_direct_encoder_create(5, &direct_frame, &context, encoded, dropped, &overflow,
                                    session, options));
  assert(!overflow && context.destroyed == 4);
  for (unsigned i = 0; i < 128; i++)
    assert(dpe_hevc_destroy(bounded[i]));
  assert(context.created == 132 && context.destroyed == 132 && !dpe_hevc_active());
  assert(dpe_hevc_level(1920, 1080, 60, 9000000) == 123);
  assert(!dpe_hevc_level(UINT32_MAX, UINT32_MAX, UINT32_MAX, 9000000));
  pthread_cond_destroy(&context.condition);
  pthread_mutex_destroy(&context.mutex);
  dlclose(module);
  return 0;
}
